"""The evaluation harness and the scenario labels, checked without calling Claude."""
from collections import Counter

from pricing_api.main import quote
from scripts.evaluate import evaluate, field_ok, to_markdown
from scripts.synthetic import build_scenarios


def test_scenarios_are_consistent_with_their_labels():
    rows = build_scenarios(200)
    assert len(rows) == 200
    kinds = Counter(r["scenario"] for r in rows)
    assert len(kinds) == 10
    for r in rows:
        for q in r["expected_requests"]:
            if r["scenario"] == "expensive":
                assert quote(q["origin"], q["destination"], q["weight_kg"]) > 5000
            elif q["origin"] and q["destination"] and q["weight_kg"]:
                assert quote(q["origin"], q["destination"], q["weight_kg"]) <= 5000


def test_rules_agree_with_labels_using_oracle_extraction():
    rows = build_scenarios(200)
    for r in rows:
        r.update(message_id=f"<{r['id']}@t>", sender="x@y.z", subject="", body="(synthetic)")
    report = evaluate(rows, "oracle")
    assert report["decision_accuracy"] == 1.0, report["errors"][:3]
    assert report["field_accuracy"]["origin"] == 1.0
    assert "Decision accuracy" in to_markdown(report)


def test_city_aliases():
    assert field_ok("origin", "Milan", "Mailand")
    assert field_ok("destination", "Antwerp", "Antwerpen")
    assert field_ok("weight_kg", 18000, 18000.0)
    assert not field_ok("origin", "Lyon", "Paris")
