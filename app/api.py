"""Workflow service: ingest emails, run the pipeline, expose state for the UI."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from . import metrics
from .config import Settings, utcnow
from .extractor import make_extractor
from .models import Email, Result
from .pipeline import Pipeline
from .pricing_client import PricingClient
from .store import Store

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "sample_emails.json"

settings = Settings()
store = Store(settings.db_path)
pricing = PricingClient(settings.pricing_url, settings.pricing_retries, settings.pricing_backoff_s, settings.pricing_timeout_s)
pipeline = Pipeline(store, make_extractor(settings.extractor, settings.extraction_model), pricing, settings)

app = FastAPI(title="WALTER email price-request workflow", version="1.0")


@app.get("/health")
def health():
    return {"ok": True, "extractor": pipeline.extractor.name, "model": settings.extraction_model,
            "today": settings.today().isoformat()}


@app.post("/emails", response_model=Result)
def ingest(email: Email):
    return pipeline.process(email)


@app.post("/demo/samples", response_model=list[Result])
def load_samples():
    out = []
    for raw in json.loads(SAMPLES.read_text(encoding="utf-8")):
        raw = {k: v for k, v in raw.items() if k != "id"}
        out.append(pipeline.process(Email.model_validate(raw)))
    return out


@app.get("/samples")
def samples():
    return json.loads(SAMPLES.read_text(encoding="utf-8"))


@app.get("/inbox")
def inbox():
    rows = store.inbox()
    for r in rows:
        r["result"] = json.loads(r.pop("shown_result")) if r.get("shown_result") else None
        for k in ("raw_json", "result_json"):
            r.pop(k, None)
    return rows


@app.post("/retry", response_model=list[Result])
def retry(advance_minutes: float = Query(0, description="Simulate time passing (demo of the 30-min rule)")):
    return pipeline.retry_pending(utcnow() + timedelta(minutes=advance_minutes))


class Order(BaseModel):
    customer: str
    origin: str
    destination: str
    weight_kg: float | None = None


@app.post("/orders")
def add_order(o: Order):
    store.add_order(o.customer, o.origin, o.destination, o.weight_kg, utcnow().isoformat())
    return {"ok": True}


@app.get("/metrics")
def get_metrics():
    return metrics.compute(store.inbox(), settings)


@app.get("/events")
def events():
    return store.events()


@app.post("/reset")
def reset():
    store.reset()
    return {"ok": True}


class FailureRate(BaseModel):
    failure_rate: float


@app.get("/pricing/config")
def pricing_config():
    try:
        return pricing.get_config()
    except Exception as e:
        raise HTTPException(502, f"pricing API unreachable: {e}") from e


@app.post("/pricing/config")
def set_pricing_config(body: FailureRate):
    try:
        return pricing.set_failure_rate(body.failure_rate)
    except Exception as e:
        raise HTTPException(502, f"pricing API unreachable: {e}") from e
