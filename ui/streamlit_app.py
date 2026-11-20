"""Operator UI: inbox, decision + reasons + JSON per email, API-failure toggle, metrics."""
from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
import pandas as pd
import streamlit as st

API = os.getenv("WORKFLOW_URL", "http://localhost:8000")
http = httpx.Client(base_url=API, timeout=60)

BADGE = {"SEND_PRICE": "🟢", "ASK_CUSTOMER": "🟡", "HANDOVER_HUMAN": "🔴", "IGNORE": "⚪", "RETRY_LATER": "🟠"}

st.set_page_config(page_title="WALTER price requests", page_icon="🚚", layout="wide")


def call(method: str, path: str, **kw):
    try:
        r = http.request(method, path, **kw)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        st.error(f"{method} {path} failed: {e}")
        return None


health = call("GET", "/health") or {}

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Controls")
    st.caption(f"Extractor: **{health.get('extractor', '?')}** ({health.get('model', '')}) · business date {health.get('today', '?')}")
    if st.button("▶ Load the 7 sample emails", use_container_width=True):
        call("POST", "/demo/samples")
    if st.button("🗑 Reset database", use_container_width=True):
        call("POST", "/reset")

    st.divider()
    st.subheader("Pricing API")
    cfg = call("GET", "/pricing/config") or {"failure_rate": 0}
    down = st.toggle("Simulate pricing API outage (100 % 503)", value=cfg["failure_rate"] >= 1)
    # Turning the outage off must bring the rate back to 0, not leave the API failing 90 % of the time.
    current = cfg["failure_rate"] if cfg["failure_rate"] < 1 else 0.0
    rate = 1.0 if down else st.slider("Random 503 rate", 0.0, 0.9, float(current), 0.1)
    if abs(rate - cfg["failure_rate"]) > 1e-9:
        call("POST", "/pricing/config", json={"failure_rate": rate})
        st.rerun()
    st.caption("Client retries 3× with 0.5 s / 1 s / 2 s backoff, then parks the email as RETRY_LATER.")
    adv = st.number_input("Retry pending, simulating minutes passed", 0, 120, 0, 5)
    if st.button("↻ Retry pending emails", use_container_width=True):
        res = call("POST", "/retry", params={"advance_minutes": adv}) or []
        st.toast(f"Retried {len(res)} email(s)")

    st.divider()
    st.subheader("Order history")
    with st.form("order"):
        cust = st.text_input("Customer", "einkauf@alpina.at")
        o, d = st.columns(2)
        origin, dest = o.text_input("From", "Linz"), d.text_input("To", "Antwerpen")
        if st.form_submit_button("Add past order"):
            call("POST", "/orders", json={"customer": cust, "origin": origin, "destination": dest})

tab_inbox, tab_new, tab_metrics = st.tabs(["📥 Inbox", "✉️ New email", "📊 Metrics"])

# ---------------------------------------------------------------- inbox
with tab_inbox:
    rows = call("GET", "/inbox") or []
    if not rows:
        st.info("Inbox is empty. Load the sample emails from the sidebar.")
    for r in reversed(rows):
        res = r.get("result") or {}
        dec = r["shown_decision"]
        title = f"{BADGE.get(dec, '')} **{dec}** · {r['sender']} · {r['subject'] or '(no subject)'}"
        if r["duplicate"]:
            title += " · _duplicate delivery_"
        with st.expander(title, expanded=False):
            left, right = st.columns([3, 2])
            with left:
                st.markdown(f"**Message-ID** `{r['message_id']}` · **thread** `{r['thread_id']}` · received {r['received_at'][:16]}")
                st.text(r["body"])
                st.markdown("**Reasons**")
                for reason in res.get("reasons", []):
                    st.markdown(f"- {reason}")
                for o in res.get("outcomes", []):
                    q = o.get("quote")
                    rq = o["request"]
                    st.markdown(f"`{rq.get('origin')} → {rq.get('destination')}` · {rq.get('pickup_date') or '—'} · "
                                f"{rq.get('weight_kg') or '—'} kg" + (f" · **{q['price']:,.2f} {q['currency']}**" if q else ""))
                if res.get("reply_draft"):
                    st.markdown("**Reply draft**")
                    st.code(res["reply_draft"], language=None)
            with right:
                st.markdown("**Extraction (LLM output)**")
                st.json(res.get("extraction") or {}, expanded=True)
                with st.popover("Full result JSON"):
                    st.json(res)

# ---------------------------------------------------------------- new email
with tab_new:
    st.caption("Without ANTHROPIC_API_KEY the recorded extractor is used, so unknown emails go to a human.")
    samples = call("GET", "/samples") or []
    pick = st.selectbox("Prefill from sample", ["—"] + [s["id"] for s in samples])
    pre = next((s for s in samples if s["id"] == pick), {})
    with st.form("new"):
        mid = st.text_input("Message-ID", pre.get("message_id", f"<{datetime.now().timestamp():.0f}@demo>"))
        sender = st.text_input("From", pre.get("sender", "kunde@example.com"))
        subject = st.text_input("Subject", pre.get("subject", ""))
        irt = st.text_input("In-Reply-To (optional)", pre.get("in_reply_to", "") or "")
        body = st.text_area("Body", pre.get("body", ""), height=150)
        if st.form_submit_button("Process"):
            out = call("POST", "/emails", json={
                "message_id": mid, "sender": sender, "subject": subject, "body": body,
                "received_at": pre.get("received_at") or datetime.now(UTC).isoformat(),
                "in_reply_to": irt or None, "references": [irt] if irt else []})
            if out:
                st.subheader(f"{BADGE.get(out['decision'], '')} {out['decision']}")
                st.json(out)

# ---------------------------------------------------------------- metrics
with tab_metrics:
    m = call("GET", "/metrics") or {}
    if m.get("emails"):
        c = st.columns(4)
        c[0].metric("Deliveries processed", m["emails"])
        c[1].metric("Handled without a human", f"{m['automation_rate']:.0%}")
        c[2].metric("Handling time", f"{m['after']['handling_minutes']:.0f} min",
                    delta=f"-{m['minutes_saved']:.0f} min vs. manual", delta_color="inverse")
        c[3].metric("Avg. processing time", f"{m['avg_processing_ms']:.0f} ms")
        st.subheader("Before / after")
        st.table(pd.DataFrame({
            "Before (manual)": [m["before"]["manual_emails"], f"{m['before']['handling_minutes']:.0f} min", m["before"]["assumption"]],
            "After (workflow)": [m["after"]["manual_emails"], f"{m['after']['handling_minutes']:.0f} min", m["after"]["assumption"]],
        }, index=["Emails needing a person", "Total handling time", "Assumption"]))
        st.subheader("Decisions")
        st.bar_chart(pd.Series(m["decisions"], name="emails"))
    else:
        st.info("No data yet.")
