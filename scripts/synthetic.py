"""Scenario catalogue for synthetic evaluation emails.

The ground-truth labels come from the scenario we sample here, not from the LLM that
writes the email text. Claude only turns a spec into a realistic email."""
from __future__ import annotations

import random
from datetime import date, timedelta

from pricing_api.main import quote

RECEIVED = date(2026, 10, 6)
LANGS = {"de": 0.35, "en": 0.30, "fr": 0.12, "it": 0.12, "es": 0.11}
CITIES = ["Linz", "Wien", "Graz", "Salzburg", "Innsbruck", "Munich", "Stuttgart", "Frankfurt", "Hamburg", "Berlin",
          "Leipzig", "Cologne", "Antwerp", "Rotterdam", "Paris", "Lyon", "Milan", "Turin", "Verona", "Bologna",
          "Prague", "Budapest", "Bratislava", "Ljubljana", "Zagreb", "Zurich", "Barcelona", "Warsaw"]
# Routes whose 24 t mock price is above the 5,000 EUR auto-send limit
FAR = [("Lisbon", "Helsinki"), ("Porto", "Bucharest"), ("Lisbon", "Stockholm"), ("Helsinki", "Lisbon"),
       ("Bucharest", "Porto"), ("Lisbon", "Warsaw"), ("Porto", "Helsinki"), ("Madrid", "Helsinki")]
OTHER_TOPICS = ["invoice correction (wrong address)", "complaint about a late delivery", "request for a POD / proof of delivery",
                "tracking question for an ongoing shipment", "change of contact person", "newsletter unsubscribe",
                "question about payment terms", "damaged goods claim"]

# scenario -> (weight, expected decision, intent)
SCENARIOS = {
    "complete":        (0.30, "SEND_PRICE", "price_request"),
    "multi_complete":  (0.08, "SEND_PRICE", "price_request"),
    "missing_weight":  (0.10, "ASK_CUSTOMER", "price_request"),
    "vague_date":      (0.10, "ASK_CUSTOMER", "price_request"),
    "missing_route":   (0.04, "ASK_CUSTOMER", "price_request"),
    "overweight":      (0.07, "HANDOVER_HUMAN", "price_request"),
    "past_date":       (0.06, "HANDOVER_HUMAN", "price_request"),
    "expensive":       (0.06, "HANDOVER_HUMAN", "price_request"),
    "reference":       (0.05, "HANDOVER_HUMAN", "price_request"),
    "other":           (0.14, "HANDOVER_HUMAN", "other"),
}


def _req(rng, origin=None, dest=None, d=None, w=None, max_price=5000):
    while True:
        o, t = origin or rng.choice(CITIES), dest or rng.choice(CITIES)
        if o == t:
            origin = dest = None
            continue
        weight = w if w is not None else rng.choice([4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24]) * 1000
        if quote(o, t, weight) <= max_price:
            break
        origin = dest = None
    day = d or RECEIVED + timedelta(days=rng.randint(2, 24))
    return {"origin": o, "destination": t, "pickup_date": day.isoformat(), "weight_kg": weight,
            "references_previous_order": False}


def build_scenarios(n: int = 200, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    names, weights = zip(*[(k, v[0]) for k, v in SCENARIOS.items()])
    out = []
    for i in range(n):
        kind = rng.choices(names, weights)[0]
        lang = rng.choices(list(LANGS), list(LANGS.values()))[0]
        _, decision, intent = SCENARIOS[kind]
        hint, reqs = "", []
        if kind == "complete":
            reqs = [_req(rng)]
        elif kind == "multi_complete":
            reqs = [_req(rng) for _ in range(rng.choice([2, 2, 3]))]
        elif kind == "missing_weight":
            reqs = [_req(rng)]
            reqs[0]["weight_kg"] = None
            hint = "Do NOT mention any weight, tonnage or number of pallets."
        elif kind == "vague_date":
            reqs = [_req(rng)]
            reqs[0]["pickup_date"] = None
            hint = "Give no concrete pick-up day; use a vague expression like 'next week', 'early November' or 'asap'."
        elif kind == "missing_route":
            reqs = [_req(rng)]
            reqs[0]["destination"] = None
            hint = "Mention only the pick-up city; do NOT say where the goods go."
        elif kind == "overweight":
            reqs = [_req(rng, w=rng.choice([26, 28, 30, 35]) * 1000)]
        elif kind == "past_date":
            reqs = [_req(rng, d=RECEIVED - timedelta(days=rng.randint(3, 20)))]
            hint = "Write the pick-up date WITH the year (e.g. 28.09.2026)."
        elif kind == "expensive":
            o, t = rng.choice(FAR)
            reqs = [_req(rng, origin=o, dest=t, w=24000, max_price=10**9)]
        elif kind == "reference":
            reqs = [{"origin": None, "destination": None, "pickup_date": None, "weight_kg": None,
                     "references_previous_order": True}]
            hint = "Refer to the route only as 'same route as last time' (in the email's language); no cities, date or weight."
        elif kind == "other":
            hint = f"Not a price request: {rng.choice(OTHER_TOPICS)}. Do not ask for any transport price."
        if kind not in ("past_date",) and reqs and any(r["pickup_date"] for r in reqs):
            hint = (hint + " Write dates as day and month without year (e.g. '14.10.' or '14 October').").strip()
        out.append({"id": f"S{i:03d}", "scenario": kind, "language": lang, "expected_intent": intent,
                    "expected_decision": decision, "expected_requests": reqs, "hint": hint,
                    "received_at": f"{RECEIVED.isoformat()}T09:00:00+00:00"})
    return out
