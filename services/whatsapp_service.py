"""
Servizio WhatsApp – invio messaggi tramite Meta Cloud API.
Ogni funzione riceve lo shop dict per usare phone_number_id
e meta_access_token del negozio (con fallback a env vars).
"""
from __future__ import annotations

import logging
import requests
from flask import current_app

log = logging.getLogger(__name__)

_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=5, pool_maxsize=10, max_retries=1,
        )
        _session.mount("https://", adapter)
    return _session


def _shop_creds(shop: dict) -> tuple[str, str]:
    """Restituisce (phone_number_id, access_token) dallo shop, fallback env."""
    pid = (shop.get("phone_number_id") or "").strip()
    token = (shop.get("meta_access_token") or "").strip()
    if not pid:
        pid = current_app.config.get("META_PHONE_NUMBER_ID", "")
    if not token:
        token = current_app.config.get("META_ACCESS_TOKEN", "")
    return pid, token


def _graph_url(pid: str) -> str:
    ver = current_app.config.get("GRAPH_API_VERSION", "v20.0")
    return f"https://graph.facebook.com/{ver}/{pid}/messages"


def _wa_post(pid: str, token: str, payload: dict) -> dict | None:
    if not pid or not token:
        log.warning("WhatsApp pid/token mancanti – non invio")
        return None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        resp = _get_session().post(_graph_url(pid), json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        log.exception("Errore invio WhatsApp pid=%s", pid)
        return None


# ── Invio messaggi ────────────────────────────────────────────

def send_text_message(shop: dict, to: str, text: str) -> dict | None:
    """Invia un messaggio di testo."""
    pid, token = _shop_creds(shop)
    return _wa_post(pid, token, {
        "messaging_product": "whatsapp", "to": to,
        "type": "text", "text": {"body": text},
    })


def send_interactive_buttons(shop: dict, to: str, body: str,
                             buttons: list[dict]) -> dict | None:
    """Invia bottoni interattivi (max 3)."""
    pid, token = _shop_creds(shop)
    btn_list = []
    for b in buttons[:3]:
        btn_list.append({
            "type": "reply",
            "reply": {"id": b.get("id", "btn"), "title": b.get("title", "OK")[:20]},
        })
    return _wa_post(pid, token, {
        "messaging_product": "whatsapp", "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button", "body": {"text": body},
            "action": {"buttons": btn_list},
        },
    })


def send_list_message(shop: dict, to: str, body: str, button_text: str,
                      rows: list[tuple[str, str, str]]) -> dict | None:
    """Invia lista interattiva. rows: [(id, title, desc), ...] max 10."""
    pid, token = _shop_creds(shop)
    items = [
        {"id": rid, "title": title[:24], "description": (desc or "")[:72]}
        for rid, title, desc in rows[:10]
    ]
    return _wa_post(pid, token, {
        "messaging_product": "whatsapp", "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list", "body": {"text": body},
            "action": {
                "button": button_text[:20],
                "sections": [{"title": "Seleziona", "rows": items}],
            },
        },
    })


def send_template_message(shop: dict, to: str, template_name: str,
                          language: str,
                          components: list | None = None) -> dict | None:
    """Invia un Message Template (funziona fuori dalla finestra 24h)."""
    pid, token = _shop_creds(shop)
    payload: dict = {
        "messaging_product": "whatsapp", "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
        },
    }
    if components:
        payload["template"]["components"] = components
    return _wa_post(pid, token, payload)


# ── Notifiche proprietario ────────────────────────────────────

def notify_owner(shop: dict, message: str) -> None:
    """Notifica il proprietario – usa template se configurato (funziona sempre)."""
    if not current_app.config.get("ENABLE_OWNER_NOTIFY", True):
        return
    from utils.helpers import norm_phone
    owner = norm_phone(shop.get("owner_phone", "") or "")
    if not owner:
        return
    template_name = (shop.get("owner_template_name") or "").strip()
    template_lang = (shop.get("owner_template_lang") or "it").strip()
    try:
        if template_name:
            components = [{
                "type": "body",
                "parameters": [{"type": "text", "text": message[:1024]}],
            }]
            send_template_message(shop, owner, template_name, template_lang, components)
        else:
            send_text_message(shop, owner, message)
    except Exception as e:
        log.warning("Notifica owner fallita shop=%s: %s", shop.get("id"), e)
