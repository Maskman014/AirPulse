import pytest
import requests

import app


@pytest.fixture
def client():
    app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    with app.app.test_client() as client:
        yield client


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._payload


AQ_PAYLOAD = {"timezone": "Asia/Kolkata", "current": {
    "time": "2026-01-01T10:00", "us_aqi": 160, "pm2_5": 70.5, "pm10": 90.0,
    "ozone": 20.0, "nitrogen_dioxide": 15.0, "sulphur_dioxide": 5.0, "carbon_monoxide": 300.0}}
GEO_PAYLOAD = {"results": [{"name": "Delhi", "latitude": 28.6, "longitude": 77.2, "country_code": "in"}]}


@pytest.fixture
def fake_http(monkeypatch):
    """Replace the shared HTTP session; records calls and clears caches around each test."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(GEO_PAYLOAD if url == app.GEOCODE_URL else AQ_PAYLOAD)

    monkeypatch.setattr(app.HTTP, "get", fake_get)
    app.AQI_CACHE.clear()
    app.GEOCODE_CACHE.clear()
    yield calls
    app.AQI_CACHE.clear()
    app.GEOCODE_CACHE.clear()


def test_home_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Air Quality Companion" in response.data


def test_health(client):
    assert client.get("/api/health").get_json()["status"] == "ok"


def test_calculator_metric(client):
    response = client.post("/api/calculator", json={
        "length": 5, "width": 4, "height": 3, "cadr": 300,
        "length_unit": "m", "cadr_unit": "m3/h"
    })
    assert response.status_code == 200
    data = response.get_json()
    assert data["volume_m3"] == 60.0
    assert data["ach"] == 5.0


def test_calculator_feet_and_cfm(client):
    response = client.post("/api/calculator", json={
        "length": 10, "width": 10, "height": 8, "cadr": 100,
        "length_unit": "ft", "cadr_unit": "cfm"
    })
    data = response.get_json()
    assert response.status_code == 200
    assert data["volume_m3"] == pytest.approx(22.65, abs=0.01)
    assert data["cadr_m3h"] == pytest.approx(169.9, abs=0.01)


def test_calculator_rejects_invalid_values(client):
    response = client.post("/api/calculator", json={
        "length": 0, "width": 4, "height": 3, "cadr": 300,
        "length_unit": "m", "cadr_unit": "m3/h"
    })
    assert response.status_code == 400


def test_co_alarm_emergency(client):
    response = client.post("/api/chat", json={"message": "The CO alarm is sounding"})
    data = response.get_json()
    assert data["emergency"] is True
    assert "fresh air immediately" in data["reply"]
    assert "Do not re-enter" in data["reply"]


def test_respiratory_emergency(client):
    response = client.post("/api/chat", json={"message": "I have chest pain and shortness of breath"})
    data = response.get_json()
    assert data["emergency"] is True
    assert "emergency" in data["reply"].lower()


def test_missing_chat_message(client):
    response = client.post("/api/chat", json={"message": " "})
    assert response.status_code == 400


def test_identity_for_south_asia():
    assert app.get_identity("IN") == "VayuMitra"
    assert app.get_identity("US") == "CleanAir Companion"


def test_aqi_category_is_not_fabricated():
    assert app.us_aqi_category(None)[0] == "Unavailable"
    assert app.us_aqi_category(50)[0] == "Good"


def test_greeting_chat_response(client):
    response = client.post("/api/chat", json={"message": "Hi"})
    data = response.get_json()
    assert response.status_code == 200
    assert data["emergency"] is False
    assert "AQI interpretation" in data["reply"]


def test_air_check_chat_response(client):
    response = client.post("/api/chat", json={"message": "check air", "location": "Delhi"})
    data = response.get_json()
    assert response.status_code == 200
    assert "live conditions" in data["reply"].lower()
    assert "HEPA" in data["reply"]


def test_unknown_chat_gets_actionable_fallback(client):
    response = client.post("/api/chat", json={"message": "What can you do?"})
    data = response.get_json()
    assert response.status_code == 200
    assert "Tell me whether" in data["reply"]


def test_aqi_by_place_and_cached(client, fake_http):
    first = client.get("/api/aqi?place=Delhi").get_json()
    second = client.get("/api/aqi?place=%20delhi%20").get_json()
    assert first["identity"] == "VayuMitra"
    assert first["category"] == "Unhealthy"
    assert first["pollutants"]["PM2.5"] == 70.5
    assert second == first
    assert len(fake_http) == 2  # one geocode + one air-quality call, the repeat is served from cache
    assert all(kwargs["timeout"] == app.HTTP_TIMEOUT for _, kwargs in fake_http)


def test_aqi_cache_keys_on_rounded_coordinates(client, fake_http):
    client.get("/api/aqi?lat=28.601&lon=77.201")
    client.get("/api/aqi?lat=28.604&lon=77.204")
    assert len(fake_http) == 1


def test_aqi_validation_errors(client, fake_http):
    assert client.get("/api/aqi").status_code == 400
    assert client.get("/api/aqi?lat=95&lon=10").status_code == 400
    assert client.get("/api/aqi?lat=nan&lon=10").status_code == 400
    assert fake_http == []


def test_aqi_upstream_failure_is_not_cached(client, monkeypatch):
    app.AQI_CACHE.clear()

    def boom(url, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(app.HTTP, "get", boom)
    response = client.get("/api/aqi?lat=1&lon=2&country=US")
    assert response.status_code == 502
    assert response.get_json()["identity"] == "CleanAir Companion"
    assert app.AQI_CACHE.get(app._rounded_coords(1, 2)) is None


def test_aqi_location_not_found(client, monkeypatch):
    app.GEOCODE_CACHE.clear()
    monkeypatch.setattr(app.HTTP, "get", lambda url, **kw: FakeResponse({}))
    assert client.get("/api/aqi?place=Nowhereville").status_code == 404
    app.GEOCODE_CACHE.clear()


def test_ttl_cache_expires_and_evicts(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(app.time, "monotonic", lambda: now[0])
    cache = app.TTLCache(ttl=10, max_entries=2)
    cache.set("a", 1)
    assert cache.get("a") == 1
    now[0] = 11
    assert cache.get("a") is None
    cache.set("a", 1), cache.set("b", 2), cache.set("c", 3)
    assert cache.get("a") is None and cache.get("c") == 3


@pytest.mark.parametrize("aqi,expected", [(51, "Moderate"), (150, "Unhealthy for sensitive groups"),
                                          (300, "Very unhealthy"), (301, "Hazardous")])
def test_aqi_category_boundaries(aqi, expected):
    assert app.us_aqi_category(aqi)[0] == expected


@pytest.mark.parametrize("payload", [
    {"length": "nan", "width": 4, "height": 3, "cadr": 300},
    {"length": 5, "width": 4, "height": 3, "cadr": "inf"},
    {"length": 5, "width": 4, "height": 3, "cadr": 300, "length_unit": "yards"},
    {"length": 5, "width": 4},
])
def test_calculator_rejects_bad_input(client, payload):
    assert client.post("/api/calculator", json=payload).status_code == 400


def test_calculator_m3_per_min(client):
    data = client.post("/api/calculator", json={
        "length": 5, "width": 4, "height": 3, "cadr": 5, "cadr_unit": "m3/min"}).get_json()
    assert data["cadr_m3h"] == 300.0 and data["ach"] == 5.0


def test_chat_fallback_uses_word_boundaries(client):
    reply = client.post("/api/chat", json={"message": "Which one is this?"}).get_json()["reply"]
    assert "Tell me whether" in reply  # "hi" inside "which"/"this" must not trigger the greeting


def test_chat_uses_llm_and_trims_history(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(app.HTTP, "post", lambda url, **kw: FakeResponse(
        {"choices": [{"message": {"content": "x" * 2000}}]}))
    for _ in range(3):
        data = client.post("/api/chat", json={"message": "tell me about filters"}).get_json()
    assert data["ai_configured"] is True and data["reply"] == "x" * 2000
    with client.session_transaction() as sess:
        assert len(sess["chat_history"]) == 2  # oldest turns dropped to stay under the cookie budget


def test_trim_history_limits():
    history = [{"role": "user", "content": "a" * 1000} for _ in range(30)]
    trimmed = app.trim_history(history)
    assert len(trimmed) == 3 and sum(len(m["content"]) for m in trimmed) <= app.HISTORY_MAX_CHARS


def test_chat_llm_failure_falls_back(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")

    def boom(url, **kw):
        raise requests.Timeout()

    monkeypatch.setattr(app.HTTP, "post", boom)
    assert "Tell me whether" in client.post("/api/chat", json={"message": "anything"}).get_json()["reply"]


def test_chat_reset_clears_history(client):
    client.post("/api/chat", json={"message": "Hi"})
    client.post("/api/chat/reset")
    with client.session_transaction() as sess:
        assert "chat_history" not in sess
