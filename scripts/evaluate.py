"""Run the pipeline over labelled synthetic emails and write an accuracy report.

    python -m scripts.evaluate --data data/synthetic/emails.jsonl            # Claude extractor
    python -m scripts.evaluate --data ... --extractor oracle                 # ground truth -> tests the rules only
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.extractor import ClaudeExtractor, ExtractionError
from app.models import Email, Extraction
from app.pipeline import Pipeline
from app.pricing_client import PricingClient
from app.store import Store
from pricing_api.main import CITIES, STATE
from pricing_api.main import app as pricing_app

DECISIONS = ["SEND_PRICE", "ASK_CUSTOMER", "HANDOVER_HUMAN", "RETRY_LATER", "IGNORE"]
FIELDS = ["origin", "destination", "pickup_date", "weight_kg"]
ALIASES = {"munich": "münchen", "cologne": "köln", "milan": "milano", "mailand": "milano", "turin": "torino",
           "vienna": "wien", "prague": "praha", "warsaw": "warszawa", "lisbon": "lisboa", "rome": "roma",
           "zurich": "zürich", "antwerp": "antwerpen", "anvers": "antwerpen", "amberes": "antwerpen",
           "varsovie": "warszawa", "varsovia": "warszawa", "varsavia": "warszawa", "praga": "praha",
           "prag": "praha", "monaco di baviera": "münchen", "múnich": "münchen",
           "vienne": "wien", "viena": "wien", "turín": "torino", "milán": "milano", "colonia": "köln", "lione": "lyon", "parigi": "paris", "parís": "paris",
           "londres": "london", "zúrich": "zürich", "zurigo": "zürich", "budapeszt": "budapest",
           "lisbonne": "lisboa", "lissabon": "lisboa", "bukarest": "bucharest", "bucarest": "bucharest",
           "bucareste": "bucharest", "bolonia": "bologna", "bologne": "bologna", "berlín": "berlin",
           "berlino": "berlin", "helsingfors": "helsinki", "estocolmo": "stockholm", "stoccolma": "stockholm",
           "turim": "torino",
           "francfort": "frankfurt", "fráncfort": "frankfurt", "francoforte": "frankfurt",
           "ratisbona": "regensburg", "amburgo": "hamburg", "hambourg": "hamburg", "hamburgo": "hamburg",
           "zagabria": "zagreb", "lubiana": "ljubljana", "liubliana": "ljubljana", "bratislavia": "bratislava",
           "rotterdão": "rotterdam", "anversa": "antwerpen", "praag": "praha", "milaan": "milano"}


def norm_city(c):
    if not c:
        return None
    c = c.strip().lower()
    c = ALIASES.get(c, c)
    loc = CITIES.get(c)
    return loc or c  # same coordinates == same city


def field_ok(f, exp, got):
    if f in ("origin", "destination"):
        return norm_city(exp) == norm_city(got)
    if f == "weight_kg":
        return (exp is None and got is None) or (exp is not None and got is not None and abs(exp - got) <= 0.01 * exp)
    return exp == got


class OracleExtractor:
    """Returns the ground truth: measures the rules in isolation (should be 100 %)."""
    name = "oracle"

    def __init__(self, rows):
        self.rows = {r["message_id"]: r for r in rows}

    def extract(self, email, open_request=None):
        r = self.rows[email.message_id]
        reqs = [{**q, "pickup_date_text": q["pickup_date"]} for q in r["expected_requests"]]
        return Extraction(intent=r["expected_intent"], language=r["language"], confidence=1.0,
                          requests=reqs if r["expected_intent"] != "other" else [], summary=r["scenario"])


class CachedExtractor:
    """Runs the real extractor concurrently up front, then serves results to the pipeline."""

    def __init__(self, inner, emails, workers=8):
        self.name = inner.name

        def one(e):
            try:
                return e.message_id, inner.extract(e)
            except ExtractionError as err:
                return e.message_id, err
        with ThreadPoolExecutor(max_workers=workers) as pool:
            self.cache = dict(pool.map(one, emails))

    def extract(self, email, open_request=None):
        r = self.cache[email.message_id]
        if isinstance(r, Exception):
            raise r
        return r


def evaluate(rows: list[dict], extractor_kind: str = "claude", model: str = "claude-haiku-5-5") -> dict:
    s = Settings()
    s.today_override = rows[0]["received_at"][:10] if rows else ""
    s.pricing_backoff_s = 0
    STATE["failure_rate"] = 0.0
    emails = [Email(message_id=r["message_id"], sender=r["sender"], subject=r.get("subject", ""), body=r["body"],
                    received_at=r["received_at"]) for r in rows]
    if extractor_kind == "oracle":
        extractor = OracleExtractor(rows)
    else:
        extractor = CachedExtractor(ClaudeExtractor(model), emails)
    p = Pipeline(Store(":memory:"), extractor, PricingClient("", http=TestClient(pricing_app)), s)

    confusion = defaultdict(Counter)
    by_lang, by_scen = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    field_hits, field_total = Counter(), Counter()
    intent_ok = count_ok = 0
    errors = []
    for row, email in zip(rows, emails):
        res = p.process(email)
        got, exp = res.decision.value, row["expected_decision"]
        confusion[exp][got] += 1
        ok = got == exp
        for bucket in (by_lang[row["language"]], by_scen[row["scenario"]]):
            bucket[0] += ok
            bucket[1] += 1
        ex = res.extraction
        intent_ok += bool(ex) and ex.intent == row["expected_intent"]
        if ex and row["expected_intent"] == "price_request":
            exp_reqs, got_reqs = row["expected_requests"], ex.requests
            count_ok += len(exp_reqs) == len(got_reqs)
            for i, er in enumerate(exp_reqs):
                gr = got_reqs[i].model_dump() if i < len(got_reqs) else {}
                for f in FIELDS:
                    field_total[f] += 1
                    field_hits[f] += field_ok(f, er.get(f), gr.get(f))
        elif row["expected_intent"] == "other":
            count_ok += bool(ex) and not ex.requests
        if not ok:
            errors.append({"id": row["id"], "scenario": row["scenario"], "language": row["language"],
                           "expected": exp, "got": got, "reasons": res.reasons,
                           "body": row["body"][:300], "extraction": ex.model_dump() if ex else None})
    n = len(rows)
    return {
        "extractor": extractor.name, "model": model if extractor_kind != "oracle" else None, "n": n,
        "decision_accuracy": sum(confusion[k][k] for k in confusion) / n if n else 0,
        "intent_accuracy": intent_ok / n if n else 0,
        "request_count_accuracy": count_ok / n if n else 0,
        "field_accuracy": {f: field_hits[f] / field_total[f] for f in FIELDS if field_total[f]},
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "by_language": {k: v[0] / v[1] for k, v in sorted(by_lang.items())},
        "by_scenario": {k: {"accuracy": v[0] / v[1], "n": v[1]} for k, v in sorted(by_scen.items())},
        "errors": errors,
    }


def to_markdown(r: dict) -> str:
    pct = lambda x: f"{x:.1%}"  # noqa: E731
    lines = [f"# Accuracy report ({r['extractor']}{' / ' + r['model'] if r['model'] else ''}, n={r['n']})", "",
             "| Metric | Value |", "|---|---|",
             f"| Decision accuracy | **{pct(r['decision_accuracy'])}** |",
             f"| Intent accuracy | {pct(r['intent_accuracy'])} |",
             f"| Request count correct | {pct(r['request_count_accuracy'])} |"]
    lines += [f"| Field `{f}` | {pct(v)} |" for f, v in r["field_accuracy"].items()]
    lines += ["", "## Confusion matrix (rows = expected, columns = decided)", "",
              "| expected \\ got | " + " | ".join(DECISIONS) + " |", "|---" * (len(DECISIONS) + 1) + "|"]
    for e in DECISIONS:
        if e in r["confusion"]:
            lines.append(f"| {e} | " + " | ".join(str(r["confusion"][e].get(g, 0)) for g in DECISIONS) + " |")
    lines += ["", "## By scenario", "", "| scenario | n | accuracy |", "|---|---|---|"]
    lines += [f"| {k} | {v['n']} | {pct(v['accuracy'])} |" for k, v in r["by_scenario"].items()]
    lines += ["", "## By language", "", "| language | accuracy |", "|---|---|"]
    lines += [f"| {k} | {pct(v)} |" for k, v in r["by_language"].items()]
    unsafe = [e for e in r["errors"] if e["got"] == "SEND_PRICE"]
    lines += ["", f"## Errors ({len(r['errors'])}; {len(unsafe)} would have auto-sent a price wrongly)", ""]
    for e in r["errors"][:40]:
        lines.append(f"- **{e['id']}** {e['scenario']}/{e['language']}: expected {e['expected']}, got {e['got']} "
                     f"- {'; '.join(e['reasons'])[:200]}")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/synthetic/emails.jsonl")
    ap.add_argument("--extractor", choices=["claude", "oracle"], default="claude")
    ap.add_argument("--model", default=os.getenv("EXTRACTION_MODEL", "claude-haiku-5-5"))
    ap.add_argument("--out", default="reports/accuracy")
    a = ap.parse_args()
    rows = [json.loads(line) for line in Path(a.data).read_text(encoding="utf-8").splitlines() if line.strip()]
    r = evaluate(rows, a.extractor, a.model)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    out.with_suffix(".md").write_text(to_markdown(r), encoding="utf-8")
    print(to_markdown(r).split("## Confusion")[0])
    print(f"full report: {out.with_suffix('.md')}")


if __name__ == "__main__":
    main()
