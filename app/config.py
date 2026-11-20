from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "data/walter.db"))
    pricing_url: str = field(default_factory=lambda: os.getenv("PRICING_URL", "http://localhost:8001"))
    # "auto" -> Claude when ANTHROPIC_API_KEY is set, otherwise recorded fixtures
    extractor: str = field(default_factory=lambda: os.getenv("EXTRACTOR", "auto"))
    extraction_model: str = field(default_factory=lambda: os.getenv("EXTRACTION_MODEL", "claude-haiku-5-5"))
    # Business rules
    min_confidence: float = field(default_factory=lambda: _env_float("MIN_CONFIDENCE", 0.8))
    max_weight_kg: float = field(default_factory=lambda: _env_float("MAX_WEIGHT_KG", 24000))
    max_auto_price_eur: float = field(default_factory=lambda: _env_float("MAX_AUTO_PRICE_EUR", 5000))
    pricing_down_handover_min: float = field(default_factory=lambda: _env_float("PRICING_DOWN_HANDOVER_MIN", 30))
    # Pricing client
    pricing_retries: int = field(default_factory=lambda: int(os.getenv("PRICING_RETRIES", "3")))
    pricing_backoff_s: float = field(default_factory=lambda: _env_float("PRICING_BACKOFF_S", 0.5))
    pricing_timeout_s: float = field(default_factory=lambda: _env_float("PRICING_TIMEOUT_S", 5))
    # Business "today" for the past-date rule. Empty = real date. The demo pins it so
    # the October sample emails are not all in the past.
    today_override: str = field(default_factory=lambda: os.getenv("APP_TODAY", ""))
    # Baseline assumptions for the before/after metrics
    manual_minutes_per_email: float = field(default_factory=lambda: _env_float("MANUAL_MIN_PER_EMAIL", 12))
    review_minutes_per_auto_email: float = field(default_factory=lambda: _env_float("REVIEW_MIN_PER_AUTO_EMAIL", 1))

    def today(self) -> date:
        return date.fromisoformat(self.today_override) if self.today_override else date.today()


def utcnow() -> datetime:
    return datetime.now(UTC)
