"""The workflow: dedupe -> understand (LLM) -> validate (rules) -> decide -> price -> draft.

Every decision is taken here in plain code; the extractor only supplies structured input.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from .config import Settings, utcnow
from .extractor import ExtractionError, Extractor
from .models import Decision, Email, ExtractedRequest, Extraction, RequestOutcome, Result
from .pricing_client import PricingClient, PricingRejected, PricingUnavailable
from .replies import ask_reply, quote_reply
from .rules import gate_extraction, merge_requests, price_reasons, resolve_reference, validate_request
from .store import Store

THREAD_STATE = {
    Decision.SEND_PRICE: "quoted",
    Decision.ASK_CUSTOMER: "awaiting_customer",
    Decision.HANDOVER_HUMAN: "handed_over",
    Decision.RETRY_LATER: "pending_retry",
}


class Pipeline:
    def __init__(self, store: Store, extractor: Extractor, pricing: PricingClient, settings: Settings | None = None):
        self.store, self.extractor, self.pricing = store, extractor, pricing
        self.s = settings or Settings()

    # ------------------------------------------------------------------ entry points
    def process(self, email: Email, now: datetime | None = None) -> Result:
        now = now or utcnow()
        t0 = time.perf_counter()

        # 1. Idempotency: the same Message-ID is processed exactly once.
        existing = self.store.get_email(email.message_id)
        if existing:
            res = Result(message_id=email.message_id, thread_id=existing["thread_id"], decision=Decision.IGNORE,
                         reasons=[(f"Duplicate delivery of Message-ID {email.message_id} "
                                  f"(already processed: {existing['decision']})")])
            self.store.add_delivery(email.message_id, True, res.model_dump(mode="json"), now.isoformat())
            self.store.log(email.message_id, "duplicate", "ignored", now.isoformat())
            return res

        # 2. Thread: join the thread of the message we reply to, else start a new one.
        thread_id = self.store.thread_of([m for m in [email.in_reply_to, *email.references] if m]) or email.message_id
        thread = self.store.get_thread(thread_id)
        open_reqs = thread.get("requests") if thread and thread["state"] == "awaiting_customer" else None

        # 3. Understand (LLM).
        try:
            ex = self.extractor.extract(email, {"requests": open_reqs} if open_reqs else None)
        except ExtractionError as e:
            res = Result(message_id=email.message_id, thread_id=thread_id, decision=Decision.HANDOVER_HUMAN,
                         reasons=[f"Extraction failed: {e}"])
            return self._finish(email, res, t0, now, thread)

        res = self._decide(email, thread_id, ex, open_reqs, thread, now)
        return self._finish(email, res, t0, now, thread)

    def retry_pending(self, now: datetime | None = None) -> list[Result]:
        """Re-run the pricing step for emails parked in RETRY_LATER (no new LLM call)."""
        now = now or utcnow()
        results = []
        for row in self.store.pending_retry():
            t0 = time.perf_counter()
            email = Email.model_validate_json(row["raw_json"])
            prev = Result.model_validate_json(row["result_json"])
            thread = self.store.get_thread(row["thread_id"])
            reqs = [o.request for o in prev.outcomes]
            base = [r for r in prev.reasons if not r.startswith(("Pricing API", "HANDOVER: pricing", "Retry at", "Price ", "Request ", "All required"))]
            res = self._validate_and_price(email, row["thread_id"], prev.extraction, reqs, base, thread, now)
            res.reasons.insert(0, f"Retry at {now.isoformat(timespec='seconds')}")
            results.append(self._finish(email, res, t0, now, thread, delivery=False))
        return results

    # ------------------------------------------------------------------ decision logic
    def _decide(self, email: Email, thread_id: str, ex: Extraction, open_reqs, thread, now) -> Result:
        def result(decision, reasons, outcomes=None, reply=None):
            return Result(message_id=email.message_id, thread_id=thread_id, decision=decision, reasons=reasons,
                          extraction=ex, outcomes=outcomes or [], reply_draft=reply)

        gate = gate_extraction(ex, self.s)
        if gate:
            return result(Decision.HANDOVER_HUMAN, gate)

        reasons: list[str] = []
        requests = list(ex.requests)
        if ex.intent == "reply_to_clarification":
            if not open_reqs:
                return result(Decision.HANDOVER_HUMAN, ["Looks like a reply to a clarification, but there is no open request in this thread"])
            requests = merge_requests(open_reqs, requests)
            reasons.append(f"Merged with open request of thread {thread_id}")
        elif open_reqs:
            reasons.append("New price request in a thread with an open request; treated as a new request")

        if not requests:
            requests = [ExtractedRequest(origin=None, destination=None, pickup_date=None, pickup_date_text=None,
                                         weight_kg=None, references_previous_order=False)]
            reasons.append("No transport details found in the email")

        return self._validate_and_price(email, thread_id, ex, requests, reasons, thread, now)

    def _validate_and_price(self, email, thread_id, ex, requests, reasons, thread, now) -> Result:
        reasons = list(reasons)

        def result(decision, extra, outcomes, reply=None):
            return Result(message_id=email.message_id, thread_id=thread_id, decision=decision,
                          reasons=reasons + extra, extraction=ex, outcomes=outcomes, reply_draft=reply)

        # Validate every request (order-history lookup, required fields, plausibility).
        outcomes: list[RequestOutcome] = []
        last_order = self.store.last_order(email.sender)
        for req in requests:
            req, info, handover = resolve_reference(req, last_order)
            o = validate_request(req, self.s, self.s.today())
            o.reasons = info + [f"HANDOVER: {h}" for h in handover] + o.reasons
            outcomes.append(o)
        tag = (lambda i, r: f"Request {i}: {r}") if len(outcomes) > 1 else (lambda i, r: r)

        handover = [tag(i, r) for i, o in enumerate(outcomes, 1) for r in o.reasons if r.startswith("HANDOVER")]
        if handover:
            return result(Decision.HANDOVER_HUMAN, handover, outcomes)

        info = [tag(i, r) for i, o in enumerate(outcomes, 1) for r in o.reasons]
        if any(o.missing for o in outcomes):
            missing = [tag(i, "missing " + ", ".join(o.missing)) for i, o in enumerate(outcomes, 1) if o.missing]
            return result(Decision.ASK_CUSTOMER, info + missing, outcomes, ask_reply(ex.language, outcomes))

        # All requests complete -> price them.
        for i, o in enumerate(outcomes, 1):
            try:
                o.quote = self.pricing.price(o.request)
            except PricingRejected as e:
                return result(Decision.HANDOVER_HUMAN, info + [tag(i, f"HANDOVER: pricing API rejected the request ({e})")], outcomes)
            except PricingUnavailable as e:
                first = (thread or {}).get("first_failure_at") if (thread or {}).get("state") == "pending_retry" else None
                first_dt = datetime.fromisoformat(first) if first else now
                down_for = now - first_dt
                if down_for >= timedelta(minutes=self.s.pricing_down_handover_min):
                    return result(Decision.HANDOVER_HUMAN, info + [
                        (f"HANDOVER: pricing API down for {down_for.total_seconds() / 60:.0f} min "
                        f"(≥ {self.s.pricing_down_handover_min:.0f} min)")], outcomes)
                r = result(Decision.RETRY_LATER, info + [f"Pricing API unavailable: {e}"], outcomes)
                r._first_failure_at = first_dt.isoformat()
                return r
            info += [tag(i, f"Price {o.quote.price:,.2f} {o.quote.currency}")]

        over = [tag(i, r) for i, o in enumerate(outcomes, 1) for r in price_reasons(o, self.s)]
        if over:
            return result(Decision.HANDOVER_HUMAN, info + over, outcomes)
        return result(Decision.SEND_PRICE, info + ["All required fields present and within limits"],
                      outcomes, quote_reply(ex.language, outcomes))

    # ------------------------------------------------------------------ persistence
    def _finish(self, email: Email, res: Result, t0: float, now: datetime, thread, delivery: bool = True) -> Result:
        res.processing_ms = int((time.perf_counter() - t0) * 1000)
        status = "pending_retry" if res.decision == Decision.RETRY_LATER else "processed"
        payload = res.model_dump(mode="json")
        self.store.save_email(email, res.thread_id, status, res.decision.value, payload, now.isoformat())
        if delivery:
            self.store.add_delivery(email.message_id, False, None, now.isoformat())
        state = THREAD_STATE[res.decision]
        reqs = [o.request.model_dump() for o in res.outcomes] or (thread or {}).get("requests")
        lang = res.extraction.language if res.extraction else (thread or {}).get("language")
        self.store.upsert_thread(res.thread_id, email.sender, state, reqs, lang, now.isoformat(),
                                 res._first_failure_at)
        self.store.log(email.message_id, res.decision.value, "; ".join(res.reasons), now.isoformat())
        return res
