"""
Servizio Clienti – gestione associazione cliente ↔ shop.
Legge/scrive sul foglio Google Sheet 'customers'.
"""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)


def upsert_customer_shop(
    phone: str,
    shop_id: str,
    customer_name: str = "",
    last_seen_phone_number_id: str = "",
    touch_updated_at: bool = True,
):
    """Crea o aggiorna il record cliente, associandolo a uno shop."""
    from services.sheets_service import upsert_customer_to_sheet
    upsert_customer_to_sheet(
        phone=phone,
        shop_id=shop_id,
        customer_name=customer_name,
        last_seen_phone_number_id=last_seen_phone_number_id,
    )
    log.info("Customer %s associato a shop %s", phone, shop_id)


def get_customer(phone: str) -> dict | None:
    """Restituisce il record del cliente, o None."""
    from services.sheets_service import get_customer_by_phone
    return get_customer_by_phone(phone)


def get_customer_shop_id(phone: str) -> str | None:
    """Restituisce lo shop_id associato al cliente."""
    rec = get_customer(phone)
    return rec["shop_id"] if rec else None
