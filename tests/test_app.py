import pytest

import app


@pytest.fixture
def client():
    app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    with app.app.test_client() as client:
        yield client


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
