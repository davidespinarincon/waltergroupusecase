"""LLM step: understand the email. It never decides - it only classifies and extracts."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

import anthropic

from .models import Email, Extraction

SYSTEM_PROMPT = """You read customer emails sent to the sales inbox of a European road-freight \
forwarder (full truck loads) and turn them into structured data. You do not decide anything and \
you do not write replies; downstream rules do that.

intent:
- "price_request": the customer asks for a price/quote/offer for one or more transports.
- "reply_to_clarification": the email answers an earlier question of ours about an open request \
(usually supplies missing details such as date or weight). Use this when "Open request in this \
thread" is given and the email adds details to it.
- "other": anything else (invoices, complaints, address changes, newsletters, ...).

Extraction rules:
- One entry in `requests` per distinct route. For a reply to a clarification, return the details \
the email supplies; copy over route fields from the open request only if the email confirms them.
- Never guess. A field the email does not state is null. Vague dates ("next week", "soon", \
"end of month") are NOT concrete: pickup_date = null, keep the wording in pickup_date_text.
- Dates: if the customer writes a year ("17.09.2026"), copy that exact date, even when it lies \
in the past - never change a date the customer wrote; downstream rules handle past dates. Only \
when the year is missing ("14.10.", "14 October") use the next occurrence on or after the \
received date.
- Cities: give the English name of the city only ("Mailand"/"Milano" -> "Milan", "Lisbonne" -> \
"Lisbon", "Bukarest" -> "Bucharest", "Wien" -> "Vienna"); drop street, postcode and country.
- Weight in kg: "24 t" -> 24000. "24 Paletten" is not a weight.
- references_previous_order = true when the route is only given by reference ("same route as \
last time", "wie letztes Mal"); leave origin/destination null in that case.
- confidence: lower it when the email is ambiguous, contradictory or you had to interpret a lot.
"""


FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixture_extractions.json"


class Extractor(Protocol):
    name: str

    def extract(self, email: Email, open_request: dict | None = None) -> Extraction: ...


class ExtractionError(RuntimeError):
    pass


def build_user_prompt(email: Email, open_request: dict | None) -> str:
    parts = [
        f"Received: {email.received_at.date().isoformat()} ({email.received_at.strftime('%A')})",
        f"From: {email.sender}",
        f"Subject: {email.subject}",
    ]
    if open_request:
        parts.append("Open request in this thread (we asked the customer for missing details):\n"
                     + json.dumps(open_request, ensure_ascii=False))
    parts.append(f"<email_body>\n{email.body}\n</email_body>")
    return "\n".join(parts)


class ClaudeExtractor:
    name = "claude"

    def __init__(self, model: str, client: anthropic.Anthropic | None = None):
        self.model = model
        self.client = client or anthropic.Anthropic(max_retries=3)

    def extract(self, email: Email, open_request: dict | None = None) -> Extraction:
        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_user_prompt(email, open_request)}],
                output_format=Extraction,
                output_config={"effort": "low"},
            )
        except anthropic.APIConnectionError as e:
            raise ExtractionError(f"Claude API unreachable: {e}") from e
        except anthropic.RateLimitError as e:
            raise ExtractionError("Claude API rate limited") from e
        except anthropic.APIStatusError as e:
            raise ExtractionError(f"Claude API error {e.status_code}") from e
        if response.stop_reason == "refusal":
            raise ExtractionError("Claude declined to process the email")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise ExtractionError(f"No structured output (stop_reason={response.stop_reason})")
        return response.parsed_output


class FixtureExtractor:
    """Replays recorded extractions (keyed by Message-ID). Used for tests, CI and the
    no-API-key demo. Unknown emails come back as low-confidence 'other' -> human."""

    name = "fixture"

    def __init__(self, path: str | Path = FIXTURES):
        self.fixtures = json.loads(Path(path).read_text(encoding="utf-8"))

    def extract(self, email: Email, open_request: dict | None = None) -> Extraction:
        if email.message_id in self.fixtures:
            return Extraction.model_validate(self.fixtures[email.message_id])
        return Extraction(intent="other", language="en", confidence=0.0, requests=[],
                          summary="No recorded extraction for this email (set ANTHROPIC_API_KEY to use Claude).")


def make_extractor(kind: str, model: str) -> Extractor:
    if kind == "claude" or (kind == "auto" and os.getenv("ANTHROPIC_API_KEY")):
        return ClaudeExtractor(model)
    return FixtureExtractor()
