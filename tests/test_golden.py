"""The 7 case-study emails (E1 delivered twice) as golden cases, run in delivery order."""
import pytest

from app.models import Decision

from .conftest import NOW, load_samples

EXPECTED = {
    "E1": Decision.SEND_PRICE,
    "E1-dup": Decision.IGNORE,
    "E2": Decision.ASK_CUSTOMER,
    "E3": Decision.SEND_PRICE,
    "E4": Decision.SEND_PRICE,
    "E5": Decision.HANDOVER_HUMAN,
    "E6": Decision.HANDOVER_HUMAN,
}


@pytest.fixture
def results(pipeline):
    return {sid: pipeline.process(mail, now=NOW) for sid, mail in load_samples()}


def test_golden_decisions(results):
    assert {k: v.decision for k, v in results.items()} == EXPECTED


def test_e1_quote_in_german(results):
    r = results["E1"]
    assert len(r.outcomes) == 1 and r.outcomes[0].quote.currency == "EUR"
    assert r.outcomes[0].quote.price <= 5000
    assert r.reply_draft.startswith("Sehr geehrte") and "Linz → Antwerpen" in r.reply_draft


def test_e1_duplicate_is_ignored_and_not_reprocessed(results, store):
    assert "Duplicate delivery" in results["E1-dup"].reasons[0]
    assert len([e for e in store.list_emails() if e["message_id"] == "<e1-7f3a@alpina.at>"]) == 1
    inbox = store.inbox()
    assert [r["shown_decision"] for r in inbox][:2] == ["SEND_PRICE", "IGNORE"]


def test_e2_asks_for_date_and_weight_in_english(results, store):
    r = results["E2"]
    assert r.outcomes[0].missing == ["pickup_date", "weight_kg"]
    assert r.reply_draft.startswith("Dear") and "exact pick-up date" in r.reply_draft
    assert store.get_thread("<e2-91bc@nordfood.de>")["state"] == "quoted"  # closed later by E3


def test_e3_is_merged_with_open_request_of_e2(results):
    r = results["E3"]
    assert r.thread_id == "<e2-91bc@nordfood.de>"
    q = r.outcomes[0].request
    assert (q.origin, q.destination, q.pickup_date, q.weight_kg) == ("Hamburg", "Milan", "2026-10-20", 12000)
    assert any("Merged with open request" in x for x in r.reasons)
    assert r.reply_draft.startswith("Dear")


def test_e4_two_routes_two_prices(results):
    r = results["E4"]
    assert [(o.request.destination, o.quote is not None) for o in r.outcomes] == [("Lyon", True), ("Paris", True)]
    assert r.reply_draft.count("Graz →") == 2


def test_e5_invoice_goes_to_human(results):
    assert "Not a price request" in results["E5"].reasons[0]
    assert results["E5"].reply_draft is None


def test_e6_unresolved_reference_goes_to_human(results):
    assert any("no order history" in x for x in results["E6"].reasons)


def test_e6_with_order_history_resolves_route_and_asks_for_rest(pipeline, store):
    store.add_order("einkauf@alpina.at", "Linz", "Antwerpen", 24000, "2026-09-01T00:00:00")
    e6 = dict(load_samples())["E6"]
    r = pipeline.process(e6, now=NOW)
    assert r.decision == Decision.ASK_CUSTOMER
    assert (r.outcomes[0].request.origin, r.outcomes[0].request.destination) == ("Linz", "Antwerpen")
    assert r.outcomes[0].missing == ["pickup_date", "weight_kg"]
