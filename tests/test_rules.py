from datetime import timedelta

import pytest

from app.extractor import ExtractionError
from app.models import Decision
from app.pipeline import Pipeline
from pricing_api.main import STATE

from .conftest import NOW, StubExtractor, email, extraction, req


def run(store, pricing, settings, ex, mail=None, now=NOW):
    return Pipeline(store, StubExtractor(ex), pricing, settings).process(mail or email(), now=now)


@pytest.mark.parametrize("ex, needle", [
    (extraction(req(weight_kg=26000)), "exceeds 24,000 kg"),
    (extraction(req(pickup_date="2026-10-01")), "in the past"),
    (extraction(req(origin="Lisboa", destination="Helsinki", weight_kg=24000)), "exceeds auto-send limit"),
    (extraction(req(), confidence=0.6), "confidence 0.60 < 0.80"),
    (extraction(intent="other"), "Not a price request"),
    (extraction(req(), intent="reply_to_clarification"), "no open request"),
])
def test_handover_rules(store, pricing, settings, ex, needle):
    r = run(store, pricing, settings, ex)
    assert r.decision == Decision.HANDOVER_HUMAN
    assert any(needle in x for x in r.reasons), r.reasons


def test_exactly_24_t_is_allowed(store, pricing, settings):
    assert run(store, pricing, settings, extraction(req(weight_kg=24000))).decision == Decision.SEND_PRICE


def test_extraction_failure_goes_to_human(store, pricing, settings):
    r = run(store, pricing, settings, ExtractionError("Claude API unreachable"))
    assert r.decision == Decision.HANDOVER_HUMAN and "Extraction failed" in r.reasons[0]


def test_vague_date_is_asked_not_guessed(store, pricing, settings):
    r = run(store, pricing, settings, extraction(req(pickup_date=None), language="fr"))
    assert r.decision == Decision.ASK_CUSTOMER
    assert r.reply_draft.startswith("Madame, Monsieur")


def test_pricing_down_retry_then_handover_after_30_min(store, pricing, settings):
    STATE["failure_rate"] = 1.0
    p = Pipeline(store, StubExtractor(extraction(req())), pricing, settings)
    r = p.process(email(), now=NOW)
    assert r.decision == Decision.RETRY_LATER
    assert pricing.last_attempts == 4  # 1 call + 3 retries
    assert store.get_email("<t1@x>")["status"] == "pending_retry"

    r2 = p.retry_pending(now=NOW + timedelta(minutes=10))
    assert r2[0].decision == Decision.RETRY_LATER

    r3 = p.retry_pending(now=NOW + timedelta(minutes=31))
    assert r3[0].decision == Decision.HANDOVER_HUMAN
    assert any("down for 31 min" in x for x in r3[0].reasons)
    assert store.pending_retry() == []


def test_pricing_recovers_on_retry(store, pricing, settings):
    STATE["failure_rate"] = 1.0
    p = Pipeline(store, StubExtractor(extraction(req())), pricing, settings)
    assert p.process(email(), now=NOW).decision == Decision.RETRY_LATER
    STATE["failure_rate"] = 0.0
    r = p.retry_pending(now=NOW + timedelta(minutes=5))[0]
    assert r.decision == Decision.SEND_PRICE and r.reply_draft


def test_backoff_is_exponential(settings):
    from fastapi.testclient import TestClient

    from app.models import ExtractedRequest
    from app.pricing_client import PricingClient, PricingUnavailable
    from pricing_api.main import app as pricing_app

    STATE["failure_rate"] = 1.0
    sleeps = []
    c = PricingClient("", retries=3, backoff_s=0.5, http=TestClient(pricing_app), sleep=sleeps.append)
    with pytest.raises(PricingUnavailable):
        c.price(ExtractedRequest.model_validate(req()))
    assert sleeps == [0.5, 1.0, 2.0]


def test_multi_request_one_incomplete_asks_only_for_that_one(store, pricing, settings):
    r = run(store, pricing, settings, extraction(req(), req(destination="Paris", weight_kg=None)))
    assert r.decision == Decision.ASK_CUSTOMER
    assert r.reasons == ["Request 2: missing weight_kg"]
    assert "Paris" in r.reply_draft and "Antwerpen" not in r.reply_draft


def test_reply_number_and_date_formats():
    from app.models import ExtractedRequest, Quote, RequestOutcome
    from app.replies import quote_reply

    o = RequestOutcome(request=ExtractedRequest.model_validate(req(weight_kg=12000)),
                       quote=Quote(price=1557.04, currency="EUR", validUntil="2026-10-14"))
    de = quote_reply("de", [o])
    assert "12.000 kg: 1.557,04 EUR" in de and "Abholung 14.10.2026" in de
    assert "12,000 kg: 1,557.04 EUR" in quote_reply("en", [o])


def test_unknown_city_goes_to_human_not_priced(store, pricing, settings):
    r = run(store, pricing, settings, extraction(req(origin="Porto", destination="Bucarest", weight_kg=24000)))
    assert r.decision == Decision.HANDOVER_HUMAN
    assert any("unknown location: Bucarest" in x for x in r.reasons), r.reasons
    assert r.reply_draft is None


def test_explicit_past_date_with_year_goes_to_human(store, pricing, settings):
    r = run(store, pricing, settings, extraction(req(pickup_date="2026-09-17")))
    assert r.decision == Decision.HANDOVER_HUMAN
    assert any("2026-09-17 is in the past" in x for x in r.reasons)
