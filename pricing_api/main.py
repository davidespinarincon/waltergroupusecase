"""Mock LKW WALTER pricing API.

POST /price {from, to, pickupDate, weightKg} -> {price, currency, validUntil}
- 400 if a field is missing / invalid
- 503 with probability FAILURE_RATE (configurable at runtime via /admin/config)
"""
from __future__ import annotations

import math
import os
import random
from datetime import date, timedelta

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

app = FastAPI(title="Mock Pricing API", version="1.0")

STATE = {"failure_rate": float(os.getenv("FAILURE_RATE", "0.0"))}

# Approximate city coordinates (lat, lon). Unknown cities are rejected with 400 unknown_location.
CITIES = {
    "linz": (48.31, 14.29), "antwerpen": (51.22, 4.40), "antwerp": (51.22, 4.40),
    "hamburg": (53.55, 9.99), "milan": (45.46, 9.19), "milano": (45.46, 9.19), "mailand": (45.46, 9.19),
    "graz": (47.07, 15.44), "lyon": (45.76, 4.84), "paris": (48.86, 2.35),
    "wien": (48.21, 16.37), "vienna": (48.21, 16.37), "munich": (48.14, 11.58), "münchen": (48.14, 11.58),
    "berlin": (52.52, 13.40), "madrid": (40.42, -3.70), "barcelona": (41.39, 2.17),
    "rome": (41.90, 12.50), "roma": (41.90, 12.50), "rom": (41.90, 12.50),
    "warsaw": (52.23, 21.01), "warszawa": (52.23, 21.01), "prague": (50.08, 14.44), "praha": (50.08, 14.44),
    "budapest": (47.50, 19.04), "rotterdam": (51.92, 4.48), "amsterdam": (52.37, 4.90),
    "brussels": (50.85, 4.35), "lisbon": (38.72, -9.14), "lisboa": (38.72, -9.14),
    "helsinki": (60.17, 24.94), "stockholm": (59.33, 18.07), "copenhagen": (55.68, 12.57),
    "zurich": (47.37, 8.54), "zürich": (47.37, 8.54), "salzburg": (47.81, 13.06),
    "innsbruck": (47.27, 11.40), "bucharest": (44.43, 26.10), "athens": (37.98, 23.73),
    "istanbul": (41.01, 28.98), "marseille": (43.30, 5.37), "turin": (45.07, 7.69), "torino": (45.07, 7.69),
    "stuttgart": (48.78, 9.18), "frankfurt": (50.11, 8.68), "cologne": (50.94, 6.96), "köln": (50.94, 6.96),
    "ljubljana": (46.06, 14.51), "zagreb": (45.81, 15.98), "bratislava": (48.15, 17.11), "sofia": (42.70, 23.32),
    "valencia": (39.47, -0.38), "porto": (41.16, -8.63), "oslo": (59.91, 10.75), "gdansk": (54.35, 18.65),
    "bologna": (44.49, 11.34), "verona": (45.44, 10.99), "dortmund": (51.51, 7.47), "leipzig": (51.34, 12.37),
}


class PriceRequest(BaseModel):
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    pickupDate: date | None = None
    weightKg: float | None = None


class UnknownLocation(KeyError):
    pass


def locate(city: str) -> tuple[float, float]:
    loc = CITIES.get(city.strip().lower())
    if loc is None:
        raise UnknownLocation(city)
    return loc


def road_km(origin: str, dest: str) -> float:
    """Never invents a distance: an unknown city is an error, not a guess."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*locate(origin), *locate(dest)))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h)) * 1.25  # road factor


def quote(origin: str, dest: str, weight_kg: float) -> float:
    km = road_km(origin, dest)
    return round(250 + km * 1.45 * (0.6 + 0.4 * min(weight_kg, 24000) / 24000), 2)


def _today() -> date:
    override = os.getenv("APP_TODAY", "")
    return date.fromisoformat(override) if override else date.today()


@app.get("/health")
def health():
    return {"ok": True, "failure_rate": STATE["failure_rate"]}


@app.get("/admin/config")
def get_config():
    return STATE


@app.post("/admin/config")
async def set_config(request: Request):
    body = await request.json()
    rate = float(body.get("failure_rate", STATE["failure_rate"]))
    STATE["failure_rate"] = max(0.0, min(1.0, rate))
    return STATE


@app.post("/price")
def price(req: PriceRequest):
    missing = [n for n, v in (("from", req.from_), ("to", req.to), ("pickupDate", req.pickupDate), ("weightKg", req.weightKg)) if v in (None, "")]
    if missing:
        raise HTTPException(status_code=400, detail={"error": "missing_fields", "fields": missing})
    if req.weightKg <= 0:
        raise HTTPException(status_code=400, detail={"error": "invalid_weight"})
    unknown = [c for c in (req.from_, req.to) if c.strip().lower() not in CITIES]
    if unknown:
        raise HTTPException(status_code=400, detail={"error": "unknown_location", "locations": unknown})
    if random.random() < STATE["failure_rate"]:
        raise HTTPException(status_code=503, detail={"error": "pricing_unavailable"})
    return {
        "price": quote(req.from_, req.to, req.weightKg),
        "currency": "EUR",
        "validUntil": (_today() + timedelta(days=7)).isoformat(),
    }
