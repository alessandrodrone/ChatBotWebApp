"""
Servizio WhatsApp – invio messaggi tramite Meta Cloud API.
Usa requests.Session per connection pooling (keep-alive).
"""
from __future__ import annotations
import logging
import requests
from flask import current_app

log = logging.getLogger(__name__)

# Session persistente per connection pooling (keep-alive HTTP)
_session: requests.Session | None = None


def _get_session() -> requests.Session:
    """Restituisce una session con connection pooling."""
    global _session
    if _session is None:
        _session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=5,
            pool_maxsize=10,
            max_retries=1,
        )
        _session.mount("https://", adapter)
    return _session


def _graph_api_base() -> str:
    """URL base Graph API con versione dalla config."""
    version = current_app.config.get("GRAPH_API_VERSION", "v20.0")
    return f"https://graph.facebook.com/{version}"


def send_text_message(to: str, text: str, phone_number_id: str | None = None):
    """Invia un messaggio di testo via WhatsApp Cloud API."""
    token = current_app.config["META_ACCESS_TOKEN"]
    pid = phone_number_id or current_app.config["META_PHONE_NUMBER_ID"]

    if not token or not pid:
        log.warning("WhatsApp token/phone_number_id mancanti – messaggio non inviato.")
        return None

    url = f"{_graph_api_base()}/{pid}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }

    try:
        resp = _get_session().post(url, json=payload, headers=headers, timeout=8)
        resp.raise_for_status()
        log.info("Messaggio inviato a %s", to)
        return resp.json()
    except requests.RequestException:
        log.exception("Errore invio messaggio a %s", to)
        return None


def send_interactive_buttons(to: str, body: str, buttons: list[dict], phone_number_id: str | None = None):
    """Invia un messaggio con bottoni interattivi."""
    token = current_app.config["META_ACCESS_TOKEN"]
    pid = phone_number_id or current_app.config["META_PHONE_NUMBER_ID"]

    if not token or not pid:
        return None

    url = f"{_graph_api_base()}/{pid}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    action_buttons = []
    for btn in buttons[:3]:
        action_buttons.append({
            "type": "reply",
            "reply": {
                "id": btn.get("id", "btn"),
                "title": btn.get("title", "OK")[:20],
            },
        })

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {"buttons": action_buttons},
        },
    }

    try:
        resp = _get_session().post(url, json=payload, headers=headers, timeout=8)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        log.exception("Errore invio bottoni a %s", to)
        return None


def send_list_message(
    to: str,
    body: str,
    button_text: str,
    rows: list[tuple[str, str, str]],
    phone_number_id: str | None = None,
):
    """
    Invia un messaggio con lista interattiva.
    rows: [(id, title, description), ...] max 10
    """
    token = current_app.config["META_ACCESS_TOKEN"]
    pid = phone_number_id or current_app.config["META_PHONE_NUMBER_ID"]
    if not token or not pid:
        return None

    url = f"{_graph_api_base()}/{pid}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    items = [
        {"id": rid, "title": title[:24], "description": (desc or "")[:72]}
        for rid, title, desc in rows[:10]
    ]
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body},
            "action": {
                "button": button_text[:20],
                "sections": [{"title": "Seleziona", "rows": items}],
            },
        },
    }

    try:
        resp = _get_session().post(url, json=payload, headers=headers, timeout=8)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        log.exception("Errore invio lista a %s", to)
        return None


def notify_owner(shop: dict, message: str, phone_number_id: str):
    """Invia una notifica al proprietario dello shop (se abilitato)."""
    if not current_app.config.get("ENABLE_OWNER_NOTIFY", True):
        return
    from utils.helpers import norm_phone
    owner = norm_phone(shop.get("owner_phone", "") or "")
    if not owner:
        return
    try:
        send_text_message(owner, message, phone_number_id)
    except Exception as e:
        log.warning("Notifica owner fallita: %s", e)
