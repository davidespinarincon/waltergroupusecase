"""Deterministic business rules. The LLM output is only ever *input* to these functions."""
from __future__ import annotations

from datetime import date

from .config import Settings
from .models import ExtractedRequest, Extraction, RequestOutcome

REQUIRED = ("origin", "destination", "pickup_date", "weight_kg")
API_FIELD = {"origin": "from", "destination": "to", "pickup_date": "pickupDate", "weight_kg": "weightKg"}


def gate_extraction(ex: Extraction, s: Settings) -> list[str]:
    """Email-level reasons to hand over before looking at individual requests."""
    reasons = []
    if ex.intent == "other":
        reasons.append(f"Not a price request (intent=other: {ex.summary})")
    if ex.confidence < s.min_confidence:
        reasons.append(f"Extraction confidence {ex.confidence:.2f} < {s.min_confidence:.2f}")
    return reasons


def merge_requests(open_reqs: list[dict], new: list[ExtractedRequest]) -> list[ExtractedRequest]:
    """Fill the gaps of the open (partial) requests with fields from the clarification reply.
    A single reply entry applies to every open request; otherwise entries are matched by position.
    Values the customer now states explicitly win over earlier ones."""
    base = [ExtractedRequest.model_validate(r) for r in open_reqs]
    if not new:
        return base
    if len(new) == 1 and len(base) > 1:
        new = new * len(base)
    merged = []
    for i in range(max(len(base), len(new))):
        b = base[i].model_dump() if i < len(base) else {}
        n = new[i].model_dump() if i < len(new) else {}
        out = dict(b)
        for k, v in n.items():
            if k == "references_previous_order":
                out[k] = bool(b.get(k)) or bool(v)
            elif v not in (None, ""):
                out[k] = v
        out.setdefault("references_previous_order", False)
        merged.append(ExtractedRequest.model_validate(out))
    return merged


def resolve_reference(req: ExtractedRequest, last_order: dict | None) -> tuple[ExtractedRequest, list[str], list[str]]:
    """'Same route as last time' -> fill the route from order history, or hand over."""
    if not req.references_previous_order or (req.origin and req.destination):
        return req, [], []
    if not last_order:
        return req, [], ["Refers to a previous order ('same route as last time') but the customer has no order history"]
    filled = req.model_copy(update={"origin": req.origin or last_order["origin"],
                                    "destination": req.destination or last_order["destination"]})
    return filled, [f"Route taken from last order #{last_order['id']} ({last_order['origin']} → {last_order['destination']})"], []


def validate_request(req: ExtractedRequest, s: Settings, today: date) -> RequestOutcome:
    out = RequestOutcome(request=req)
    out.missing = [f for f in REQUIRED if getattr(req, f) in (None, "")]
    if req.pickup_date:
        try:
            d = date.fromisoformat(req.pickup_date)
            if d < today:
                out.reasons.append(f"HANDOVER: pickup date {d.isoformat()} is in the past")
        except ValueError:
            out.missing.append("pickup_date")
            out.request = req.model_copy(update={"pickup_date": None})
    if req.weight_kg is not None:
        if req.weight_kg <= 0:
            out.missing.append("weight_kg")
        elif req.weight_kg > s.max_weight_kg:
            out.reasons.append(f"HANDOVER: weight {req.weight_kg:,.0f} kg exceeds {s.max_weight_kg:,.0f} kg (not a standard FTL)")
    return out


def price_reasons(outcome: RequestOutcome, s: Settings) -> list[str]:
    q = outcome.quote
    if q and q.price > s.max_auto_price_eur:
        return [f"HANDOVER: price {q.price:,.2f} {q.currency} exceeds auto-send limit {s.max_auto_price_eur:,.0f} EUR"]
    return []


def is_handover(outcome: RequestOutcome) -> bool:
    return any(r.startswith("HANDOVER") for r in outcome.reasons)
