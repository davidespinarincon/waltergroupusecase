from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr


class Email(BaseModel):
    message_id: str
    sender: str
    subject: str = ""
    body: str
    received_at: datetime
    in_reply_to: str | None = None
    references: list[str] = Field(default_factory=list)


# ---- LLM output schema (structured output) -------------------------------------

class ExtractedRequest(BaseModel):
    origin: str | None = Field(description="Pickup city: English city name only (e.g. 'Milan', not 'Mailand'). null if not stated.")
    destination: str | None = Field(description="Delivery city: English city name only. null if not stated.")
    pickup_date: str | None = Field(description="Concrete pickup date as ISO YYYY-MM-DD. Keep a year the customer wrote, even if in the past; infer the year only when missing. null if no concrete day is given (e.g. 'next week').")
    pickup_date_text: str | None = Field(description="The date expression exactly as the customer wrote it, or null.")
    weight_kg: float | None = Field(description="Total weight in kilograms (1 t = 1000 kg). null if not stated.")
    references_previous_order: bool = Field(description="True if the customer refers to an earlier order/route instead of stating it (e.g. 'same route as last time').")


class Extraction(BaseModel):
    intent: Literal["price_request", "reply_to_clarification", "other"]
    language: str = Field(description="ISO 639-1 code of the email's language, e.g. 'de', 'en'.")
    confidence: float = Field(description="0.0-1.0: how sure you are that intent and every extracted field are correct.")
    requests: list[ExtractedRequest] = Field(description="One entry per distinct transport (route). Empty for 'other'.")
    summary: str = Field(description="One short English sentence describing the email.")


# ---- Decisions -----------------------------------------------------------------

class Decision(str, Enum):
    SEND_PRICE = "SEND_PRICE"
    ASK_CUSTOMER = "ASK_CUSTOMER"
    HANDOVER_HUMAN = "HANDOVER_HUMAN"
    IGNORE = "IGNORE"
    RETRY_LATER = "RETRY_LATER"


class Quote(BaseModel):
    price: float
    currency: str
    validUntil: str


class RequestOutcome(BaseModel):
    request: ExtractedRequest
    missing: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    quote: Quote | None = None


class Result(BaseModel):
    message_id: str
    thread_id: str
    decision: Decision
    reasons: list[str]
    extraction: Extraction | None = None
    outcomes: list[RequestOutcome] = Field(default_factory=list)
    reply_draft: str | None = None
    processing_ms: int = 0
    _first_failure_at: str | None = PrivateAttr(default=None)
