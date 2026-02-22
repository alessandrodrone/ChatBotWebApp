"""
Servizio Sessione – tiene traccia dello stato conversazione.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_sessions: dict[str, dict] = {}


def get_session(phone: str) -> dict:
    """Restituisce la sessione corrente o ne crea una nuova."""
    if phone not in _sessions:
        _sessions[phone] = {
            "phone": phone,
            "step": "start",
            "data": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    return _sessions[phone]


def update_session(phone: str, step: str, data: dict | None = None):
    """Aggiorna lo step e i dati della sessione."""
    session = get_session(phone)
    session["step"] = step
    if data:
        session["data"].update(data)
    session["updated_at"] = datetime.now(timezone.utc).isoformat()


def clear_session(phone: str):
    """Rimuove la sessione del cliente."""
    _sessions.pop(phone, None)
