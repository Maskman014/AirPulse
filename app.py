"""VayuMitra / CleanAir Companion: Flask backend for live AQI, purifier sizing, and chat."""
from __future__ import annotations

import math
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Final, Mapping

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from werkzeug.datastructures import MultiDict

load_dotenv()

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
SOUTH_ASIA: Final[frozenset[str]] = frozenset({"IN", "PK", "BD", "NP", "LK"})
IDENTITY_SOUTH_ASIA: Final[str] = "VayuMitra"
IDENTITY_DEFAULT: Final[str] = "CleanAir Companion"

GEOCODE_URL: Final[str] = "https://open-meteo.com"
AIR_QUALITY_URL: Final[str] = "https://open-meteo.com"
SOURCE_NAME: Final[str] = "Open-Meteo Air Quality API"
SOURCE_URL: Final[str] = "https://open-meteo.com"
STATION_NOTE: Final[str] = "Modeled grid output; confirm against the nearest official regulatory station"
SCALE_LABEL: Final[str] = "US EPA-style AQI (air-quality model field)"
UNITS_NOTE: Final[str] = "µg/m³ for listed pollutants; AQI uses the reported US AQI scale"

POLLUTANT_FIELDS: Final[dict[str, str]] = {
    "PM2.5": "pm2_5",
    "PM10": "pm10",
    "O3": "ozone",
    "NO2": "nitrogen_dioxide",
    "SO2": "sulphur_dioxide",
    "CO": "carbon_monoxide",
}
CURRENT_FIELDS: Final[str] = ",".join([*POLLUTANT_FIELDS.values(), "us_aqi"])

HTTP_TIMEOUT: Final[tuple[float, float]] = (3.05, 10.0)
LLM_TIMEOUT: Final[tuple[float, float]] = (3.05, 30.0)
HTTP_RETRIES: Final[int] = 1
HTTP_BACKOFF_FACTOR: Final[float] = 0.3
RETRY_STATUS_CODES: Final[tuple[int, ...]] = (502, 503, 504)
POOL_CONNECTIONS: Final[int] = 4
POOL_MAXSIZE: Final[int] = 10

AQI_CACHE_TTL: Final[int] = 10 * 60
GEOCODE_CACHE_TTL: Final[int] = 24 * 60 * 60
CACHE_MAX_ENTRIES: Final[int] = 256
COORD_DECIMALS: Final[int] = 2

MAX_MESSAGE_LEN: Final[int] = 4000
HISTORY_CONTEXT_MESSAGES: Final[int] = 12
HISTORY_STORED_MESSAGES: Final[int] = 20
HISTORY_MAX_CHARS: Final[int] = 3000
MIN_TRIMMED_MESSAGES: Final[int] = 2

LLM_DEFAULT_MODEL: Final[str] = "gpt-4o-mini"
LLM_DEFAULT_BASE_URL: Final[str] = "https://openai.com"
LLM_TEMPERATURE: Final[float] = 0.3
LLM_MAX_TOKENS: Final[int] = 700

FT_TO_M: Final[float] = 0.3048
CFM_TO_M3H: Final[float] = 1.699
MINUTES_PER_HOUR: Final[float] = 60.0
CADR_TO_M3H: Final[dict[str, float]] = {"m3/h": 1.0, "m3/min": MINUTES_PER_HOUR, "cfm": CFM_TO_M3H}
LENGTH_TO_M: Final[dict[str, float]] = {"m": 1.0, "ft": FT_TO_M}
CLEAN_FRACTION: Final[float] = 0.63
CALC_WARNING: Final[str] = (
    "Rated CADR varies by test pollutant, fan speed, housing seals, and room mixing. "
    "Use actual operating-speed CADR if available."
)

LAT_MIN: Final[float] = -90.0
LAT_MAX: Final[float] = 90.0
LON_MIN: Final[float] = -180.0
LON_MAX: Final[float] = 180.0

AQI_BANDS: Final[tuple[tuple[float, str, str], ...]] = (
    (50, "Good", "Enjoy normal outdoor activity."),
    (100, "Moderate", "Sensitive people may consider reducing prolonged outdoor exertion."),
    (150, "Unhealthy for sensitive groups", "Sensitive groups should reduce prolonged or heavy outdoor exertion."),
    (200, "Unhealthy", "Reduce outdoor exertion; consider a well-fitted certified particulate respirator when outside."),
    (300, "Very unhealthy", "Avoid prolonged outdoor exertion and use a clean indoor room."),
)
AQI_HAZARDOUS: Final[tuple[str, str]] = (
    "Hazardous",
    "Avoid outdoor activity and follow local public-health or emergency authority instructions.",
)
AQI_UNAVAILABLE: Final[tuple[str, str]] = ("Unavailable", "Live values could not be retrieved.")

CO_PATTERN: Final[re.Pattern[str]] = re.compile(r"\b(co|carbon monoxide)\b|carbon-monoxide")
CO_ALARM_WORDS: Final[tuple[str, ...]] = ("alarm", "beep", "detector")
CO_SYMPTOMS: Final[tuple[str, ...]] = ("headache", "dizziness", "nausea", "confusion", "vomiting")
RESPIRATORY_RED_FLAGS: Final[tuple[str, ...]] = (
    "can't breathe",
    "cannot breathe",
    "shortness of breath",
    "chest pain",
    "chest tight",
    "blue lips",
    "bluish lips",
    "fainting",
    "passed out",
    "severe wheezing",
)
EMERGENCY_REPLIES: Final[dict[str, str]] = {
    "co": (
        "⚠️ **Possible carbon-monoxide emergency:** Move everyone outdoors into fresh air immediately "
        "and call emergency services. CO is odorless. Do not re-enter, switch appliances, investigate, "
        "or retrieve belongings; wait for responders or qualified authorities to declare the building safe."
    ),
    "respiratory": (
        "🚨 **This may be a respiratory emergency.** Call your local emergency number now and do not "
        "wait for an AQI reading. Use emergency services, move to clean air if it can be reached "
        "safely, and follow dispatcher instructions."
    ),
}

FALLBACK_RULES: Final[tuple[tuple[re.Pattern[str], str], ...]] = tuple(
    (re.compile(pattern), reply)
    for pattern, reply in (
        (
            r"\b(check air|aqi|air quality|pollution|safe indoor|indoor steps)\b",
            "For live conditions, enter a city in the Dashboard to verify the source, station or model grid, "
            "timestamp, scale, and pollutants{where}. Meanwhile, reduce infiltration during particulate spikes, "
            "run a suitable HEPA purifier, avoid candles/incense/smoking, and use outdoor-vented exhaust for "
            "combustion fumes.",
        ),
        (
            r"\b(purifier|cadr|ach)\b",
            "Use the Purifier Calculator to enter the room dimensions and the purifier's CADR. ACH = CADR divided "
            "by room volume in compatible units. CADR values depend on test pollutant and fan speed, and the "
            "actual result depends on placement and air leakage.",
        ),
        (
            r"\bmasks?\b",
            "For PM2.5 or smoke, use a certified, well-fitted N95/FFP2/KF94 respirator; fit and leakage matter. "
            "Cloth and loose surgical masks are not equivalent fine-particle protection. A particulate mask does "
            "not protect against CO or unknown toxic gases.",
        ),
        (
            r"\b(ventilat\w*|windows?)\b",
            "Balance actual outdoor pollutant readings against indoor cooking, moisture, and CO2. During verified "
            "outdoor pollution, reduce infiltration and run proper filtration. Do not block combustion air or "
            "flues, and never run combustion equipment indoors.",
        ),
        (
            r"\b(hello|hi|hey|help)\b",
            "I can help with AQI interpretation, indoor filtration, ventilation, certified respirators, and "
            "purifier sizing. For location-specific live readings, enter a city in the Dashboard. For a CO alarm "
            "or breathing emergency, use the immediate P0 guidance instead of continuing this chat.",
        ),
    )
)
FALLBACK_DEFAULT: Final[str] = (
    "Tell me whether you want location-specific air quality, clean indoor steps, ventilation "
    "advice, a mask fit, or purifier sizing. If you have a CO alarm or severe breathing symptoms, "
    "leave or seek emergency care immediately; do not wait for AQI information."
)

SYSTEM_PROMPT: Final[str] = """
# System Instruction Precedence
P0—Life Safety: Emergency symptoms (shortness of breath, chest pain/tightness, bluish lips/face, severe wheezing, confusion, fainting, rapid worsening) require immediate emergency care. Suspected CO or sounding CO alarm: everyone evacuates outdoors immediately, calls emergency services, and does not re-enter until authorities declare it safe. Never delay evacuation to investigate or retrieve belongings. Follow official evacuation/shelter instructions.
P1—Environmental Integrity: Use recent location-relevant official or established environmental data. State source, station proximity, timestamp, pollutant, and scale. Never fabricate readings or compare unlike scales. PM2.5/PM10: indoor exposure reduction, HEPA, fitted N95/FFP2/KF94. Ozone: check readings and reduce exertion; particle masks do not block ozone. NO2/SO2: control combustion and use outdoor-vented exhaust; HEPA does not remove gases. Industrial releases require substance-specific authority guidance and never improvised chemical masks. Never recommend sealing so tightly that combustion appliances or flues are starved or blocked. Do not disturb extensive mold or dry-brush it.
P2—Regional identity and tone: Use VayuMitra in India, Pakistan, Bangladesh, Nepal, or Sri Lanka; otherwise CleanAir Companion. Remain nameless until location is explicitly stated or reliably established. Be calm, practical, empathetic, and evidence-aware. Ask at most two concise questions for broad requests. For the first comprehensive action plan use: Immediate Protection; Clean-Air Room and Indoor Strategy; Low-Emission Daily Swaps; Community and Shared Action. Adult respirators do not fit young children; under two never wear masks. Maintain contextual chat history.
Live AQI and pollutant guidance must be proportionate and never imply certainty where none exists.
""".strip()

DEFAULT_SECRET_KEY: Final[str] = "development-only-change-this-secret"
DEFAULT_HOST: Final[str] = "127.0.0.1"
DEFAULT_PORT: Final[int] = 5000

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", DEFAULT_SECRET_KEY)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# --------------------------------------------------------------------------- #
# HTTP session and caching
# --------------------------------------------------------------------------- #
class TTLCache:
class TTLCache:
    """Small thread-safe, size-bounded cache whose entries expire after ttl seconds."""

    def __init__(self, ttl: float, max_entries: int = CACHE_MAX_ENTRIES) -> None:
        """Initialize the cache with a time-to-live in seconds and maximum capacity."""
        self._ttl = ttl
        self._max = max_entries
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any | None:
        """Return the cached value, or None if missing or expired."""
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            if item[0] < time.monotonic():
                del self._data[key]
                return None
            return item[1]

    def set(self, key: Any, value: Any) -> None:
        """Store value, evicting the oldest entry when full."""
        with self._lock:
            self._data[key] = (time.monotonic() + self._ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear(self) -> None:
        """Remove all cached entries."""
        with self._lock:
            self._data.clear()


def _build_http_session() -> requests.Session:
    """Create one pooled, retrying session reused by every outbound request."""
    http = requests.Session()
    retry = Retry(
        total=HTTP_RETRIES,
        backoff_factor=HTTP_BACKOFF_FACTOR,
        status_forcelist=RETRY_STATUS_CODES,
        allowed_methods=frozenset({"GET"}),
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=POOL_CONNECTIONS,
        pool_maxsize=POOL_MAXSIZE,
    )
    http.mount("https://", adapter)
    return http


HTTP: Final[requests.Session] = _build_http_session()
AQI_CACHE: Final[TTLCache] = TTLCache(AQI_CACHE_TTL)
GEOCODE_CACHE: Final[TTLCache] = TTLCache(GEOCODE_CACHE_TTL)


class ApiError(Exception):
    """Raised by helpers to return a JSON error response with status."""

    def __init__(self, message: str, status: int = 400, **extra: Any) -> None:
        """Initialize an API error with an HTTP status code and optional metadata."""
        super().__init__(message)
        self.message = message
        self.status = status
        self.extra = extra


@app.errorhandler(ApiError)
def handle_api_error(err: ApiError) -> tuple[Response, int]:
    """Serialize an ApiError exception into a JSON HTTP response."""
    return jsonify({"error": err.message, **err.extra}), err.status


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def get_identity(country_code: str | None) -> str:
    """Return the assistant's regional name for an ISO country code."""
    return IDENTITY_SOUTH_ASIA if (country_code or "").upper() in SOUTH_ASIA else IDENTITY_DEFAULT


def is_emergency(text: str) -> dict[str, bool]:
    """Flag possible carbon-monoxide or respiratory emergencies in text."""
    lowered = text.lower()
    co_alarm = any(word in lowered for word in CO_ALARM_WORDS)
    co_context = bool(CO_PATTERN.search(lowered)) or co_alarm
    co_cue = co_alarm or "leak" in lowered or any(symptom in lowered for symptom in CO_SYMPTOMS)
    return {
        "co": co_context and co_cue,
        "respiratory": any(flag in lowered for flag in RESPIRATORY_RED_FLAGS),
    }


def emergency_response(kind: str) -> str:
    """Return the fixed emergency message for 'co' or 'respiratory'."""
    return EMERGENCY_REPLIES["co" if kind == "co" else "respiratory"]


def us_aqi_category(aqi: float | None) -> tuple[str, str]:
    """Map a US AQI value to (category, advice)."""
    if aqi is None:
        return AQI_UNAVAILABLE
    for upper, category, advice in AQI_BANDS:
        if aqi <= upper:
            return category, advice
    return AQI_HAZARDOUS


def _rounded_coords(lat: float, lon: float) -> tuple[float, float]:
    """Round coordinates to COORD_DECIMALS places for cache key normalization."""
    return round(lat, COORD_DECIMALS), round(lon, COORD_DECIMALS)


# --------------------------------------------------------------------------- #
# Open-Meteo access (cached)
# --------------------------------------------------------------------------- #
def geocode(query: str) -> dict[str, Any] | None:
    """Look up a place name; results (including misses) are cached for a day."""
    key = " ".join(query.lower().split())
    cached = GEOCODE_CACHE.get(key)
    if cached is not None:
        return cached or None
    resp = HTTP.get(
        GEOCODE_URL,
        params={"name": query, "count": 1, "language": "en", "format": "json"},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []
    location = results[0] if results else None
    GEOCODE_CACHE.set(key, location or {})
    return location


def fetch_air_quality(lat: float, lon: float) -> dict[str, Any]:
    """Fetch current air quality for coordinates, cached by rounded (lat, lon)."""
    key = _rounded_coords(lat, lon)
    cached = AQI_CACHE.get(key)
    if cached is not None:
        return cached
    resp = HTTP.get(
        AIR_QUALITY_URL,
        params={
            "latitude": key[0],
            "longitude": key[1],
            "current": CURRENT_FIELDS,
            "timezone": "auto",
        },
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    AQI_CACHE.set(key, data)
    return data


def resolve_location(args: MultiDict[str, str]) -> tuple[float, float, str, str]:
    """Resolve request args to (lat, lon, place, country_code) or raise ApiError."""
    place = args.get("place", "").strip()
    country = args.get("country", "").upper()
    lat = args.get("lat", type=float)
    lon = args.get("lon", type=float)

    if lat is None or lon is None:
        if not place:
            raise ApiError("Provide a place name or latitude and longitude.")
        try:
            location = geocode(place)
        except requests.RequestException:
            raise ApiError("The location search service could not be reached.", 502) from None
        if not location:
            raise ApiError("Location not found.", 404)
        lat, lon = float(location["latitude"]), float(location["longitude"])
        place = location.get("name", place)
        country = location.get("country_code", country).upper()

    is_valid_lat = math.isfinite(lat) and LAT_MIN <= lat <= LAT_MAX
    is_valid_lon = math.isfinite(lon) and LON_MIN <= lon <= LON_MAX
    if not (is_valid_lat and is_valid_lon):
        raise ApiError("Invalid coordinates.")
    return lat, lon, place, country


def build_aqi_payload(data: dict[str, Any], lat: float, lon: float, place: str, country: str) -> dict[str, Any]:
    """Shape an Open-Meteo response into the API payload sent to the browser."""
    current = data.get("current") or {}
    aqi = current.get("us_aqi")
    category, advice = us_aqi_category(aqi)
    return {
        "identity": get_identity(country),
        "location": place or f"{lat:.4f}, {lon:.4f}",
        "country_code": country,
        "coordinates": {"lat": lat, "lon": lon},
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "station": STATION_NOTE,
        "timestamp": current.get("time"),
        "timezone": data.get("timezone"),
        "scale": SCALE_LABEL,
        "aqi": aqi,
        "category": category,
        "advice": advice,
        "pollutants": {name: current.get(field) for name, field in POLLUTANT_FIELDS.items()},
        "units": UNITS_NOTE,
    }


# --------------------------------------------------------------------------- #
# Purifier calculator
# --------------------------------------------------------------------------- #
def parse_calculator_input(body: Mapping[str, Any]) -> tuple[float, float]:
    """Validate a calculator request; return (volume_m3, cadr_m3h) or raise ApiError."""
    unit = body.get("length_unit", "m")
    cadr_unit = body.get("cadr_unit", "m3/h")
    try:
        length, width, height, cadr = (float(body[k]) for k in ("length", "width", "height", "cadr"))
    except (KeyError, TypeError, ValueError):
        raise ApiError("All dimensions and CADR are required.") from None
    if unit not in LENGTH_TO_M or cadr_unit not in CADR_TO_M3H:
        raise ApiError("Unsupported unit.")
    if not all(math.isfinite(val) and val > 0 for val in (length, width, height, cadr)):
        raise ApiError("Dimensions and CADR must be greater than zero.")
    volume_m3 = (length * width * height) * (LENGTH_TO_M[unit] ** 3)
    return volume_m3, cadr * CADR_TO_M3H[cadr_unit]


def purifier_metrics(volume_m3: float, cadr_m3h: float) -> dict[str, Any]:
    """Compute air changes per hour and minutes to clean ~63% of a well-mixed room."""
    ach = cadr_m3h / volume_m3
    minutes_63 = -math.log(1 - CLEAN_FRACTION) / ach * MINUTES_PER_HOUR
    return {
        "volume_m3": round(volume_m3, 2),
        "cadr_m3h": round(cadr_m3h, 2),
        "ach": round(ach, 2),
        "approx_time_to_63_percent_clean": round(minutes_63, 1),
        "warning": CALC_WARNING,
    }
# --------------------------------------------------------------------------- #
# Chat
# --------------------------------------------------------------------------- #
def fallback_reply(message: str, location: str) -> str:
    """Return a safe canned reply when no LLM is configured or reachable."""
    lowered = message.lower()
    where = f" for {location}" if location else " after entering a city or location"
    for pattern, reply in FALLBACK_RULES:
        if pattern.search(lowered):
            return reply.format(where=where)
    return FALLBACK_DEFAULT


def ask_llm(api_key: str, history: list[dict[str, str]], country_code: str, location: str) -> str | None:
    """Query the OpenAI-compatible chat endpoint; return None on any failure."""
    base_url = os.getenv("OPENAI_BASE_URL", LLM_DEFAULT_BASE_URL).rstrip("/")
    context = (
        f"Current region identity: {get_identity(country_code)}. "
        f"Confirmed location: {location or 'not established'}."
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": context},
        *history[-HISTORY_CONTEXT_MESSAGES:],
    ]
    try:
        resp = HTTP.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": os.getenv("OPENAI_MODEL", LLM_DEFAULT_MODEL),
                "messages": messages,
                "temperature": LLM_TEMPERATURE,
                "max_tokens": LLM_MAX_TOKENS,
            },
            timeout=LLM_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["choices"]["message"]["content"] or None
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
        return None


def trim_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep recent messages within the count and size limits of a cookie session."""
    trimmed = history[-HISTORY_STORED_MESSAGES:]
    while len(trimmed) > MIN_TRIMMED_MESSAGES and sum(len(msg["content"]) for msg in trimmed) > HISTORY_MAX_CHARS:
        trimmed = trimmed[1:]
    return trimmed


def _process_chat_turn(message: str, location: str, country_code: str) -> dict[str, Any]:
    """Validate a chat message, check emergencies, and generate an assistant reply."""
    identity = get_identity(country_code)
    if not message:
        raise ApiError("Message is required.")
    if len(message) > MAX_MESSAGE_LEN:
        raise ApiError("Message is too long.")

    flags = is_emergency(message)
    for kind in ("co", "respiratory"):
        if flags[kind]:
            return {"reply": emergency_response(kind), "emergency": True, "identity": identity}

    history: list[dict[str, str]] = session.get("chat_history", [])
    history.append({"role": "user", "content": message})

    api_key = os.getenv("OPENAI_API_KEY")
    reply = (ask_llm(api_key, history, country_code, location) if api_key else None) or fallback_reply(
        message, location
    )

    history.append({"role": "assistant", "content": reply})
    session["chat_history"] = trim_history(history)
    return {"reply": reply, "emergency": False, "identity": identity, "ai_configured": bool(api_key)}


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.route("/")
def index() -> str:
    """Render the main single-page dashboard."""
    return render_template("index.html")


@app.route("/api/aqi")
def api_aqi() -> Response:
    """Return current modeled air quality data for a place name or coordinates."""
    lat, lon, place, country = resolve_location(request.args)
    try:
        data = fetch_air_quality(lat, lon)
    except (requests.RequestException, ValueError):
        raise ApiError(
            "Live air-quality data could not be retrieved.",
            502,
            identity=get_identity(country),
        ) from None
    return jsonify(build_aqi_payload(data, lat, lon, place, country))


@app.route("/api/calculator", methods=["POST"])
def calculator() -> Response:
    """Compute room air changes per hour (ACH) and clean-air time from purifier CADR."""
    volume_m3, cadr_m3h = parse_calculator_input(request.get_json(silent=True) or {})
    return jsonify(purifier_metrics(volume_m3, cadr_m3h))


@app.route("/api/chat", methods=["POST"])
def chat() -> Response:
    """Handle an interactive assistant chat turn with P0 emergency screening."""
    body = request.get_json(silent=True) or {}
    message = str(body.get("message", "")).strip()
    location = str(body.get("location", "")).strip()
    country_code = str(body.get("country_code", "")).upper()
    return jsonify(_process_chat_turn(message, location, country_code))


@app.route("/api/chat/reset", methods=["POST"])
def reset_chat() -> Response:
    """Clear the stored chat history from the user's session."""
    session.pop("chat_history", None)
    return jsonify({"status": "ok"})


@app.route("/api/health")
def health() -> Response:
    """Return a lightweight health-check response for deployment monitoring."""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", DEFAULT_HOST),
        port=int(os.getenv("PORT", str(DEFAULT_PORT))),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
