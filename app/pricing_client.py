from __future__ import annotations

import time

import httpx

from .models import ExtractedRequest, Quote


class PricingUnavailable(RuntimeError):
    """5xx / timeout / connection error after all retries."""


class PricingRejected(RuntimeError):
    """4xx - the request itself is wrong; retrying will not help."""


def _describe_rejection(r: httpx.Response) -> str:
    try:
        detail = r.json().get("detail", {})
    except ValueError:
        detail = {}
    if isinstance(detail, dict) and detail.get("error") == "unknown_location":
        return "unknown location: " + ", ".join(detail.get("locations", []))
    return f"HTTP {r.status_code}: {r.text[:200]}"


class PricingClient:
    def __init__(self, base_url: str, retries: int = 3, backoff_s: float = 0.5,
                 timeout_s: float = 5.0, http: httpx.Client | None = None, sleep=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.backoff_s = backoff_s
        self.http = http or httpx.Client(timeout=timeout_s)
        self.sleep = sleep
        self.last_attempts = 0

    def price(self, req: ExtractedRequest) -> Quote:
        payload = {"from": req.origin, "to": req.destination,
                   "pickupDate": req.pickup_date, "weightKg": req.weight_kg}
        last_error = "unknown"
        self.last_attempts = 0
        for attempt in range(self.retries + 1):  # 1 call + N retries
            self.last_attempts = attempt + 1
            if attempt:
                self.sleep(self.backoff_s * 2 ** (attempt - 1))  # 0.5s, 1s, 2s
            try:
                r = self.http.post(f"{self.base_url}/price", json=payload)
            except httpx.HTTPError as e:
                last_error = f"{type(e).__name__}"
                continue
            if r.status_code == 200:
                return Quote.model_validate(r.json())
            if 400 <= r.status_code < 500:
                raise PricingRejected(_describe_rejection(r))
            last_error = f"HTTP {r.status_code}"
        raise PricingUnavailable(f"pricing API failed after {self.last_attempts} attempts ({last_error})")

    def set_failure_rate(self, rate: float) -> dict:
        return self.http.post(f"{self.base_url}/admin/config", json={"failure_rate": rate}).json()

    def get_config(self) -> dict:
        return self.http.get(f"{self.base_url}/admin/config").json()
