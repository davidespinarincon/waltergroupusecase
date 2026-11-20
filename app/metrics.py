from __future__ import annotations

import json
from collections import Counter

from .config import Settings

AUTOMATED = {"SEND_PRICE", "ASK_CUSTOMER", "IGNORE"}


def compute(inbox: list[dict], s: Settings) -> dict:
    decisions = Counter(r["shown_decision"] for r in inbox)
    n = len(inbox)
    automated = sum(v for k, v in decisions.items() if k in AUTOMATED)
    handover = decisions.get("HANDOVER_HUMAN", 0)
    pending = decisions.get("RETRY_LATER", 0)
    before_min = n * s.manual_minutes_per_email
    after_min = automated * s.review_minutes_per_auto_email + (handover + pending) * s.manual_minutes_per_email
    latencies = [json.loads(r["shown_result"]).get("processing_ms", 0) for r in inbox if r["shown_result"]]
    return {
        "emails": n,
        "decisions": dict(decisions),
        "automation_rate": automated / n if n else 0.0,
        "before": {"manual_emails": n, "handling_minutes": before_min,
                   "assumption": f"{s.manual_minutes_per_email:g} min manual handling per email"},
        "after": {"manual_emails": handover + pending, "handling_minutes": after_min,
                  "assumption": f"{s.review_minutes_per_auto_email:g} min spot-check per automated email, "
                                f"{s.manual_minutes_per_email:g} min per handover"},
        "minutes_saved": before_min - after_min,
        "avg_processing_ms": sum(latencies) / len(latencies) if latencies else 0,
    }
