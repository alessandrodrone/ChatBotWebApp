"""
Servizio Sessione – tiene traccia dello stato conversazione.
Include TTL automatico e dedup messaggi WhatsApp.
"""
from __future__ import annotations

import logging
import datetime as dt

from flask import current_app

log = logging.getLogger(__name__)

# ── Sessioni conversazione ────────────────────────────────────
_sessions: dict[str, dict] = {}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def get_session(key: str) -> dict:
    """
    Restituisce la sessione corrente.
    Ritorna dict vuoto se non esiste o è scaduta (TTL).
    key = "shop_id:phone" per supporto multi-tenant.
    """
    s = _sessions.get(key)
    if not s:
        return {}
    ttl = current_app.config.get("SESSION_TTL_MINUTES", 45)
    if (_now() - s["_ts"]).total_seconds() / 60 > ttl:
        _sessions.pop(key, None)
        return {}
    return dict(s)


def save_session(key: str, data: dict):
    """Salva (sovrascrive) la sessione."""
    _sessions[key] = {"_ts": _now(), **data}


def clear_session(key: str):
    """Rimuove la sessione."""
    _sessions.pop(key, None)


# ── Dedup messaggi WhatsApp ──────────────────────────────────
_processed_msg_ids: dict[str, dt.datetime] = {}


def _gc_processed(ttl_minutes: int = 90):
    cut = _now() - dt.timedelta(minutes=ttl_minutes)
    for k, ts in list(_processed_msg_ids.items()):
        if ts < cut:
            del _processed_msg_ids[k]


def seen_message(message_id: str) -> bool:
    """Ritorna True se il messaggio è già stato processato (dedup)."""
    _gc_processed()
    if not message_id:
        return False
    if message_id in _processed_msg_ids:
        return True
    _processed_msg_ids[message_id] = _now()
    return False
