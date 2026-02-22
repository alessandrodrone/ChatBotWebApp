"""
Servizio Clienti – gestione associazione cliente ↔ shop.
Per ora salva in memoria; in futuro scriverà su Google Sheet (tab customers).
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# In-memory store (sarà sostituito da Google Sheets)
_customers: dict[str, dict] = {}


def upsert_customer_shop(
    phone: str,
    shop_id: str,
    customer_name: str = "",
    last_seen_phone_number_id: str = "",
    touch_updated_at: bool = True,
):
    """Crea o aggiorna il record cliente, associandolo a uno shop."""
    now = datetime.now(timezone.utc).isoformat()

    if phone in _customers:
        rec = _customers[phone]
        rec["shop_id"] = shop_id
        if customer_name:
            rec["name"] = customer_name
        if last_seen_phone_number_id:
            rec["last_seen_phone_number_id"] = last_seen_phone_number_id
        if touch_updated_at:
            rec["updated_at"] = now
    else:
        _customers[phone] = {
            "phone": phone,
            "shop_id": shop_id,
            "name": customer_name,
            "last_seen_phone_number_id": last_seen_phone_number_id,
            "created_at": now,
            "updated_at": now,
        }

    log.info("Customer %s associato a shop %s", phone, shop_id)


def get_customer(phone: str) -> dict | None:
    """Restituisce il record del cliente, o None."""
    return _customers.get(phone)


def get_customer_shop_id(phone: str) -> str | None:
    """Restituisce lo shop_id associato al cliente."""
    rec = _customers.get(phone)
    return rec["shop_id"] if rec else None
