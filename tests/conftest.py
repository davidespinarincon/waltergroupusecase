import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.extractor import FixtureExtractor
from app.models import Email, Extraction
from app.pipeline import Pipeline
from app.pricing_client import PricingClient
from app.store import Store
from pricing_api.main import STATE
from pricing_api.main import app as pricing_app

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def load_samples() -> list[tuple[str, Email]]:
    raw = json.loads((ROOT / "data" / "sample_emails.json").read_text(encoding="utf-8"))
    return [(r.pop("id"), Email.model_validate(r)) for r in raw]


@pytest.fixture
def settings():
    s = Settings()
    s.today_override = "2026-10-07"
    s.pricing_backoff_s = 0
    return s


@pytest.fixture(autouse=True)
def pricing_ok():
    STATE["failure_rate"] = 0.0
    yield
    STATE["failure_rate"] = 0.0


@pytest.fixture
def pricing(settings):
    return PricingClient("", retries=settings.pricing_retries, backoff_s=0, http=TestClient(pricing_app))


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def pipeline(store, pricing, settings):
    return Pipeline(store, FixtureExtractor(), pricing, settings)


class StubExtractor:
    """Returns a hand-written extraction regardless of the email."""
    name = "stub"

    def __init__(self, extraction: dict | Exception):
        self.extraction = extraction

    def extract(self, email, open_request=None):
        if isinstance(self.extraction, Exception):
            raise self.extraction
        return Extraction.model_validate(self.extraction)


def req(origin="Linz", destination="Antwerpen", pickup_date="2026-10-14", weight_kg=10000, ref=False):
    return {"origin": origin, "destination": destination, "pickup_date": pickup_date, "pickup_date_text": pickup_date,
            "weight_kg": weight_kg, "references_previous_order": ref}


def extraction(*requests, intent="price_request", confidence=0.95, language="de"):
    return {"intent": intent, "language": language, "confidence": confidence, "summary": "test",
            "requests": list(requests)}


def email(mid="<t1@x>", sender="kunde@example.com", body="...", **kw):
    return Email(message_id=mid, sender=sender, body=body, received_at=NOW, **kw)
