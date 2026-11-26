from __future__ import annotations

import os
import re
import math
from datetime import datetime, timezone
from typing import Any

import requests
from flask import Flask, jsonify, render_template, request, session
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "development-only-change-this-secret")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

SOUTH_ASIA = {"IN", "PK", "BD", "NP", "LK"}
SYSTEM_PROMPT = """
# System Instruction Precedence
P0—Life Safety: Emergency symptoms (shortness of breath, chest pain/tightness, bluish lips/face, severe wheezing, confusion, fainting, rapid worsening) require immediate emergency care. Suspected CO or sounding CO alarm: everyone evacuates outdoors immediately, calls emergency services, and does not re-enter until authorities declare it safe. Never delay evacuation to investigate or retrieve belongings. Follow official evacuation/shelter instructions.
P1—Environmental Integrity: Use recent location-relevant official or established environmental data. State source, station proximity, timestamp, pollutant, and scale. Never fabricate readings or compare unlike scales. PM2.5/PM10: indoor exposure reduction, HEPA, fitted N95/FFP2/KF94. Ozone: check readings and reduce exertion; particle masks do not block ozone. NO2/SO2: control combustion and use outdoor-vented exhaust; HEPA does not remove gases. Industrial releases require substance-specific authority guidance and never improvised chemical masks. Never recommend sealing so tightly that combustion appliances or flues are starved or blocked. Do not disturb extensive mold or dry-brush it.
P2—Regional identity and tone: Use VayuMitra in India, Pakistan, Bangladesh, Nepal, or Sri Lanka; otherwise CleanAir Companion. Remain nameless until location is explicitly stated or reliably established. Be calm, practical, empathetic, and evidence-aware. Ask at most two concise questions for broad requests. For the first comprehensive action plan use: Immediate Protection; Clean-Air Room and Indoor Strategy; Low-Emission Daily Swaps; Community and Shared Action. Adult respirators do not fit young children; under two never wear masks. Maintain contextual chat history.
Live AQI and pollutant guidance must be proportionate and never imply certainty where none exists.
""".strip()

LIVE_TIMEOUT = 12


def get_identity(country_code: str | None) -> str:
    return "VayuMitra" if (country_code or "").upper() in SOUTH_ASIA else "CleanAir Companion"


def is_emergency(text: str) -> dict[str, bool]:
    t = text.lower()
    co_words = re.search(r"\b(co|carbon monoxide)\b|carbon-monoxide", t)
    co_alarm = "alarm" in t or "beep" in t or "detector" in t
    co_symptoms = any(x in t for x in ("headache", "dizziness", "nausea", "confusion", "vomiting"))
    red_flags = any(x in t for x in (
        "can't breathe", "cannot breathe", "shortness of breath", "chest pain", "chest tight",
        "blue lips", "bluish lips", "fainting", "passed out", "severe wheezing",
    ))
    return {"co": bool((co_words or co_alarm) and (co_alarm or co_symptoms or "leak" in t)), "respiratory": red_flags}


def emergency_response(kind: str) -> str:
    if kind == "co":
        return ("⚠️ **Possible carbon-monoxide emergency:** Move everyone outdoors into fresh air immediately "
                "and call emergency services. CO is odorless. Do not re-enter, switch appliances, investigate, or retrieve belongings; wait for responders or qualified authorities to declare the building safe.")
    return ("🚨 **This may be a respiratory emergency.** Call your local emergency number now and do not wait for an AQI reading. "
            "Use emergency services, move to clean air if it can be reached safely, and follow dispatcher instructions.")


def geocode(query: str) -> dict[str, Any] | None:
    r = requests.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": query, "count": 1, "language": "en", "format": "json"}, timeout=LIVE_TIMEOUT)
    r.raise_for_status()
    results = r.json().get("results") or []
    return results[0] if results else None


def us_aqi_category(aqi: float | None) -> tuple[str, str]:
    if aqi is None:
        return "Unavailable", "Live values could not be retrieved."
    if aqi <= 50:
        return "Good", "Enjoy normal outdoor activity."
    if aqi <= 100:
        return "Moderate", "Sensitive people may consider reducing prolonged outdoor exertion."
    if aqi <= 150:
        return "Unhealthy for sensitive groups", "Sensitive groups should reduce prolonged or heavy outdoor exertion."
    if aqi <= 200:
        return "Unhealthy", "Reduce outdoor exertion; consider a well-fitted certified particulate respirator when outside."
    if aqi <= 300:
        return "Very unhealthy", "Avoid prolonged outdoor exertion and use a clean indoor room."
    return "Hazardous", "Avoid outdoor activity and follow local public-health or emergency authority instructions."


def latest_hourly(current: dict[str, Any], hourly: dict[str, Any]) -> dict[str, Any]:
    target = current.get("time")
    result: dict[str, Any] = {}
    if target:
        target_dt = datetime.fromisoformat(str(target).replace("Z", "+00:00"))
        for key, values in hourly.items():
            times = hourly.get("time", [])
            if not isinstance(values, list) or not times:
                continue
            idx = min(range(len(times)), key=lambda i: abs((datetime.fromisoformat(times[i].replace("Z", "+00:00"))-target_dt).total_seconds()))
            result[key] = values[idx] if idx < len(values) else None
    return result


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/aqi")
def api_aqi():
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    place = request.args.get("place", "").strip()
    country_code = request.args.get("country", "").upper()

    if lat is None or lon is None:
        if not place:
            return jsonify({"error": "Provide a place name or latitude and longitude."}), 400
        try:
            location = geocode(place)
        except requests.RequestException:
            return jsonify({"error": "The location search service could not be reached."}), 502
        if not location:
            return jsonify({"error": "Location not found."}), 404
        lat, lon = float(location["latitude"]), float(location["longitude"])
        place = location.get("name", place)
        country_code = location.get("country_code", country_code).upper()

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return jsonify({"error": "Invalid coordinates."}), 400

    try:
        r = requests.get("https://air-quality-api.open-meteo.com/v1/air-quality", params={
            "latitude": lat,
            "longitude": lon,
            "current": "pm2_5,pm10,nitrogen_dioxide,sulphur_dioxide,ozone,carbon_monoxide,us_aqi",
            "timezone": "auto",
        }, timeout=LIVE_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException:
        return jsonify({"error": "Live air-quality data could not be retrieved.", "identity": get_identity(country_code)}), 502

    current = data.get("current", {})
    aqi = current.get("us_aqi")
    category, advice = us_aqi_category(aqi)
    timestamp = current.get("time")

    return jsonify({
        "identity": get_identity(country_code),
        "location": place or f"{lat:.4f}, {lon:.4f}",
        "country_code": country_code,
        "coordinates": {"lat": lat, "lon": lon},
        "source": "Open-Meteo Air Quality API",
        "source_url": "https://open-meteo.com/en/docs/air-quality-api",
        "station": "Modeled grid output; confirm against the nearest official regulatory station",
        "timestamp": timestamp,
        "timezone": data.get("timezone"),
        "scale": "US EPA-style AQI (air-quality model field)",
        "aqi": aqi,
        "category": category,
        "advice": advice,
        "pollutants": {
            "PM2.5": current.get("pm2_5"), "PM10": current.get("pm10"),
            "O3": current.get("ozone"), "NO2": current.get("nitrogen_dioxide"),
            "SO2": current.get("sulphur_dioxide"), "CO": current.get("carbon_monoxide"),
        },
        "units": "µg/m³ for listed pollutants; AQI uses the reported US AQI scale",
    })


@app.route("/api/calculator", methods=["POST"])
def calculator():
    body = request.get_json(silent=True) or {}
    try:
        length = float(body["length"])
        width = float(body["width"])
        height = float(body["height"])
        cadr = float(body["cadr"])
        unit = body.get("length_unit", "m")
        cadr_unit = body.get("cadr_unit", "m3/h")
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "All dimensions and CADR are required."}), 400

    if length <= 0 or width <= 0 or height <= 0 or cadr <= 0:
        return jsonify({"error": "Dimensions and CADR must be greater than zero."}), 400

    factor = 0.3048 if unit == "ft" else 1.0
    volume_m3 = length * factor * width * factor * height * factor
    if cadr_unit == "cfm":
        cadr_m3h = cadr * 1.699
    elif cadr_unit == "m3/min":
        cadr_m3h = cadr * 60
    else:
        cadr_m3h = cadr
    ach = cadr_m3h / volume_m3
    hours_63 = math.log(1 - 0.63) * -1 / ach if ach > 0 else None

    return jsonify({
        "volume_m3": round(volume_m3, 2),
        "cadr_m3h": round(cadr_m3h, 2),
        "ach": round(ach, 2),
        "approx_time_to_63_percent_clean": round(hours_63 * 60, 1) if hours_63 else None,
        "warning": "Rated CADR varies by test pollutant, fan speed, housing seals, and room mixing. Use actual operating-speed CADR if available.",
    })


@app.route("/api/chat", methods=["POST"])
def chat():
    body = request.get_json(silent=True) or {}
    message = str(body.get("message", "")).strip()
    location = str(body.get("location", "")).strip()
    country_code = str(body.get("country_code", "")).upper()
    if not message:
        return jsonify({"error": "Message is required."}), 400
    if len(message) > 4000:
        return jsonify({"error": "Message is too long."}), 400

    flags = is_emergency(message)
    if flags["co"]:
        return jsonify({"reply": emergency_response("co"), "emergency": True, "identity": get_identity(country_code)})
    if flags["respiratory"]:
        return jsonify({"reply": emergency_response("respiratory"), "emergency": True, "identity": get_identity(country_code)})

    history = session.setdefault("chat_history", [])
    history.append({"role": "user", "content": message})

    reply = None
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "system", "content": f"Current region identity: {get_identity(country_code)}. Confirmed location: {location or 'not established'}."}] + history[-12:]
        try:
            r = requests.post(f"{base_url.rstrip('/')}/chat/completions", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"model": model, "messages": messages, "temperature": 0.3, "max_tokens": 700}, timeout=30)
            r.raise_for_status()
            reply = r.json()["choices"][0]["message"]["content"]
        except (requests.RequestException, KeyError, IndexError, ValueError):
            reply = None

    if not reply:
        lower = message.lower()
        if any(x in lower for x in ("check air", "aqi", "air quality", "pollution", "safe indoor", "indoor steps")):
            location_text = f" for {location}" if location else " after entering a city or location"
            reply = (f"For live conditions, enter a city in the Dashboard to verify the source, station or model grid, timestamp, scale, and pollutants{location_text}. "
                     "Meanwhile, reduce infiltration during particulate spikes, run a suitable HEPA purifier, avoid candles/incense/smoking, and use outdoor-vented exhaust for combustion fumes.")
        elif "purifier" in lower or "cadr" in lower or "ach" in lower:
            reply = ("Use the Purifier Calculator to enter the room dimensions and the purifier's CADR. ACH = CADR divided by room volume in compatible units. "
                     "CADR values depend on test pollutant and fan speed, and the actual result depends on placement and air leakage.")
        elif "mask" in lower:
            reply = ("For PM2.5 or smoke, use a certified, well-fitted N95/FFP2/KF94 respirator; fit and leakage matter. "
                     "Cloth and loose surgical masks are not equivalent fine-particle protection. A particulate mask does not protect against CO or unknown toxic gases.")
        elif "ventilat" in lower or "window" in lower:
            reply = ("Balance actual outdoor pollutant readings against indoor cooking, moisture, and CO2. During verified outdoor pollution, reduce infiltration and run proper filtration. "
                     "Do not block combustion air or flues, and never run combustion equipment indoors.")
        elif any(x in lower for x in ("hello", "hi", "hey", "help")):
            reply = ("I can help with AQI interpretation, indoor filtration, ventilation, certified respirators, and purifier sizing. "
                     "For location-specific live readings, enter a city in the Dashboard. For a CO alarm or breathing emergency, use the immediate P0 guidance instead of continuing this chat.")
        else:
            reply = ("Tell me whether you want location-specific air quality, clean indoor steps, ventilation advice, a mask fit, or purifier sizing. "
                     "If you have a CO alarm or severe breathing symptoms, leave or seek emergency care immediately; do not wait for AQI information.")

    history.append({"role": "assistant", "content": reply})
    session["chat_history"] = history[-20:]
    return jsonify({"reply": reply, "emergency": False, "identity": get_identity(country_code), "ai_configured": bool(api_key)})


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG", "0") == "1")
