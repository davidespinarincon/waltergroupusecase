from fastapi.testclient import TestClient

from pricing_api.main import STATE, app

c = TestClient(app)
BODY = {"from": "Linz", "to": "Antwerpen", "pickupDate": "2026-10-14", "weightKg": 24000}


def test_price_ok():
    r = c.post("/price", json=BODY)
    assert r.status_code == 200
    j = r.json()
    assert j["currency"] == "EUR" and j["price"] > 0 and j["validUntil"]


def test_missing_fields_400():
    r = c.post("/price", json={"from": "Linz"})
    assert r.status_code == 400
    assert set(r.json()["detail"]["fields"]) == {"to", "pickupDate", "weightKg"}


def test_configurable_503():
    c.post("/admin/config", json={"failure_rate": 1})
    assert c.post("/price", json=BODY).status_code == 503
    c.post("/admin/config", json={"failure_rate": 0})
    assert c.post("/price", json=BODY).status_code == 200
    assert STATE["failure_rate"] == 0


def test_unknown_city_is_rejected_not_guessed():
    r = c.post("/price", json={**BODY, "to": "Bucarest"})
    assert r.status_code == 400
    assert r.json()["detail"] == {"error": "unknown_location", "locations": ["Bucarest"]}
