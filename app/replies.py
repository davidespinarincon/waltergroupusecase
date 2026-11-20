"""Reply drafts in the customer's language. Templates, not free LLM text: prices and
questions must be exactly what the rules decided."""
from __future__ import annotations

from .models import RequestOutcome

T = {
    "de": {
        "greet": "Sehr geehrte Damen und Herren,",
        "thanks": "vielen Dank für Ihre Anfrage.",
        "quote_intro": "gerne bieten wir Ihnen folgende Transporte an:",
        "quote_line": "{o} → {d}, Abholung {date}, {w} kg: {p} {c} (gültig bis {valid})",
        "ask_intro": "für ein verbindliches Angebot benötigen wir noch folgende Angaben:",
        "ask_route": "Transport {o} → {d}",
        "fields": {"origin": "Abholort", "destination": "Zielort", "pickup_date": "konkretes Abholdatum", "weight_kg": "Gewicht (kg)"},
        "close": "Mit freundlichen Grüßen\nIhr LKW WALTER Team",
        "unknown": "unbekannt",
    },
    "en": {
        "greet": "Dear Sir or Madam,",
        "thanks": "thank you for your request.",
        "quote_intro": "we are pleased to offer the following transports:",
        "quote_line": "{o} → {d}, pick-up {date}, {w} kg: {p} {c} (valid until {valid})",
        "ask_intro": "to give you a binding quote we still need the following details:",
        "ask_route": "Transport {o} → {d}",
        "fields": {"origin": "pick-up location", "destination": "delivery location", "pickup_date": "exact pick-up date", "weight_kg": "weight (kg)"},
        "close": "Kind regards\nYour LKW WALTER team",
        "unknown": "unknown",
    },
    "fr": {
        "greet": "Madame, Monsieur,",
        "thanks": "nous vous remercions de votre demande.",
        "quote_intro": "nous avons le plaisir de vous proposer les transports suivants :",
        "quote_line": "{o} → {d}, enlèvement le {date}, {w} kg : {p} {c} (valable jusqu'au {valid})",
        "ask_intro": "pour vous faire une offre ferme, il nous manque encore les informations suivantes :",
        "ask_route": "Transport {o} → {d}",
        "fields": {"origin": "lieu d'enlèvement", "destination": "lieu de livraison", "pickup_date": "date d'enlèvement exacte", "weight_kg": "poids (kg)"},
        "close": "Cordialement\nVotre équipe LKW WALTER",
        "unknown": "inconnu",
    },
    "it": {
        "greet": "Gentili Signore e Signori,",
        "thanks": "grazie per la vostra richiesta.",
        "quote_intro": "siamo lieti di offrirvi i seguenti trasporti:",
        "quote_line": "{o} → {d}, ritiro il {date}, {w} kg: {p} {c} (valido fino al {valid})",
        "ask_intro": "per un'offerta vincolante ci servono ancora i seguenti dati:",
        "ask_route": "Trasporto {o} → {d}",
        "fields": {"origin": "luogo di carico", "destination": "luogo di scarico", "pickup_date": "data di ritiro precisa", "weight_kg": "peso (kg)"},
        "close": "Cordiali saluti\nIl vostro team LKW WALTER",
        "unknown": "sconosciuto",
    },
    "es": {
        "greet": "Estimados señores:",
        "thanks": "muchas gracias por su solicitud.",
        "quote_intro": "nos complace ofrecerles los siguientes transportes:",
        "quote_line": "{o} → {d}, recogida el {date}, {w} kg: {p} {c} (válido hasta {valid})",
        "ask_intro": "para una oferta vinculante necesitamos todavía los siguientes datos:",
        "ask_route": "Transporte {o} → {d}",
        "fields": {"origin": "lugar de recogida", "destination": "lugar de entrega", "pickup_date": "fecha de recogida exacta", "weight_kg": "peso (kg)"},
        "close": "Atentamente\nSu equipo de LKW WALTER",
        "unknown": "desconocido",
    },
}


def _lang(lang: str) -> str:
    code = (lang or "en").lower()[:2]
    return code if code in T else "en"


def _t(lang: str) -> dict:
    return T[_lang(lang)]


def _num(x: float, lang: str, decimals: int) -> str:
    s = f"{x:,.{decimals}f}"
    code = _lang(lang)
    if code == "en":
        return s
    sep = "\u202f" if code == "fr" else "."
    return s.replace(",", "§").replace(".", ",").replace("§", sep)


def _date(iso: str, lang: str) -> str:
    y, m, d = iso[:10].split("-")
    code = _lang(lang)
    if code == "en":
        return iso[:10]
    return f"{d}/{m}/{y}" if code in ("fr", "it", "es") else f"{d}.{m}.{y}"


def quote_reply(lang: str, outcomes: list[RequestOutcome]) -> str:
    t = _t(lang)
    lines = [t["greet"], "", f"{t['thanks']} {t['quote_intro'][0].upper()}{t['quote_intro'][1:]}", ""]
    for o in outcomes:
        r, q = o.request, o.quote
        lines.append("- " + t["quote_line"].format(o=r.origin, d=r.destination, date=_date(r.pickup_date, lang),
                                                   w=_num(r.weight_kg, lang, 0), p=_num(q.price, lang, 2),
                                                   c=q.currency, valid=_date(q.validUntil, lang)))
    lines += ["", t["close"]]
    return "\n".join(lines)


def ask_reply(lang: str, outcomes: list[RequestOutcome]) -> str:
    t = _t(lang)
    lines = [t["greet"], "", f"{t['thanks']} {t['ask_intro'][0].upper()}{t['ask_intro'][1:]}", ""]
    for o in outcomes:
        if not o.missing:
            continue
        r = o.request
        if len(outcomes) > 1 or r.origin or r.destination:
            lines.append(t["ask_route"].format(o=r.origin or t["unknown"], d=r.destination or t["unknown"]) + ":")
        lines += [f"- {t['fields'][f]}" for f in dict.fromkeys(o.missing)]
    lines += ["", t["close"]]
    return "\n".join(lines)
