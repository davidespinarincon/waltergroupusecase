"""Generate ~200 synthetic customer emails with Claude, labelled by construction.

    python -m scripts.generate_synthetic --n 200 --out data/synthetic/emails.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic
from pydantic import BaseModel

from .synthetic import build_scenarios

SYSTEM = """You write realistic emails that business customers send to the sales inbox of a European \
road-freight forwarder. Follow each spec exactly: language, every fact given (cities, dates, weights) \
and every constraint in `hint`. Vary tone, length and style (terse one-liners, polite letters, \
signatures, typos, mixed units like '18 t' / '18.000 kg' / '18 tons'). Never add facts that are not in the spec."""


class GeneratedEmail(BaseModel):
    id: str
    sender: str
    subject: str
    body: str


class Batch(BaseModel):
    emails: list[GeneratedEmail]


def generate_batch(client: anthropic.Anthropic, model: str, specs: list[dict]) -> list[GeneratedEmail]:
    public = [{k: s[k] for k in ("id", "language", "expected_requests", "hint")} | {"kind": s["scenario"]} for s in specs]
    resp = client.messages.parse(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content":
                   "Write one email per spec. Return the same ids. The sender is a plausible company address.\n"
                   + json.dumps(public, ensure_ascii=False, indent=1)}],
        output_format=Batch,
        output_config={"effort": "low"},
    )
    if resp.stop_reason != "end_turn" or resp.parsed_output is None:
        raise RuntimeError(f"generation failed: stop_reason={resp.stop_reason}")
    return resp.parsed_output.emails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--batch", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--model", default=os.getenv("GEN_MODEL", "claude-opus-5-5"))
    ap.add_argument("--out", default="data/synthetic/emails.jsonl")
    a = ap.parse_args()

    specs = build_scenarios(a.n, a.seed)
    client = anthropic.Anthropic(max_retries=4)
    batches = [specs[i:i + a.batch] for i in range(0, len(specs), a.batch)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        generated = [e for b in pool.map(lambda b: generate_batch(client, a.model, b), batches) for e in b]
    by_id = {g.id: g for g in generated}

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out.open("w", encoding="utf-8") as f:
        for s in specs:
            g = by_id.get(s["id"])
            if not g:
                print(f"warning: no email generated for {s['id']}")
                continue
            row = {**s, "message_id": f"<{s['id'].lower()}@synthetic.test>", "sender": g.sender,
                   "subject": g.subject, "body": g.body}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1
    print(f"wrote {written} emails to {out}")


if __name__ == "__main__":
    main()
