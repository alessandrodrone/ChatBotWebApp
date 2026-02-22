"""
Servizio Clienti – gestione associazione cliente ↔ shop.
Legge/scrive sul foglio Google Sheet 'customers'.
"""
from __future__ import annotations

import logging
import datetime as dt
from typing import Optional

from utils.helpers import norm_phone, norm_text, parse_iso_dt, now_utc, utc_now_iso

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
    """Restituisce lo shop_id associato al cliente (con TTL se configurato)."""
    from flask import current_app
    rec = get_customer(phone)
    if not rec:
        return None
    sid = norm_text(rec.get("shop_id", ""))
    if not sid:
        return None

    ttl_days = current_app.config.get("CUSTOMER_SHOP_TTL_DAYS", 0)
    if ttl_days > 0:
        ts = parse_iso_dt(rec.get("updated_at") or "")
        if ts:
            age_days = (now_utc() - ts).total_seconds() / 86400.0
            if age_days > ttl_days:
                return None
    return sid


def update_customer_after_booking(
    customer_phone: str,
    shop_id: str,
    service_name: str,
    start_dt: dt.datetime,
    *,
    customer_name: Optional[str] = None,
    last_seen_phone_number_id: Optional[str] = None,
):
    """Aggiorna i campi del cliente dopo una prenotazione confermata."""
    phone = norm_phone(customer_phone)
    sid = norm_text(shop_id)
    if not phone or not sid:
        return

    from services.sheets_service import get_customer_by_phone, upsert_customer_to_sheet

    rec = get_customer_by_phone(phone) or {}
    total_visits = int(rec.get("total_visits", 0) or 0) + 1
    last_visit = start_dt.replace(microsecond=0).isoformat()

    # Aggiorna via upsert
    upsert_customer_to_sheet(
        phone=phone,
        shop_id=sid,
        customer_name=customer_name or rec.get("customer_name", ""),
        last_seen_phone_number_id=last_seen_phone_number_id or "",
    )

    # Aggiorna campi specifici booking (last_service, total_visits, last_visit)
    try:
        from flask import current_app
        from services.sheets_service import _get_worksheet

        tab_name = current_app.config.get("CUSTOMERS_TAB", "customers")
        sheet = _get_worksheet(tab_name)
        if sheet is None:
            return

        cell = sheet.find(phone, in_column=2)
        if cell:
            row_num = cell.row
            updates = [
                {"range": f"C{row_num}", "values": [[service_name]]},
                {"range": f"D{row_num}", "values": [[str(total_visits)]]},
                {"range": f"E{row_num}", "values": [[last_visit]]},
            ]
            sheet.batch_update(updates, value_input_option="USER_ENTERED")
            log.info("Customer %s aggiornato dopo booking (visite: %s)", phone, total_visits)

            # Invalida cache
            from services.sheets_service import invalidate_customers_cache
            invalidate_customers_cache()
    except Exception:
        log.exception("Errore aggiornamento customer post-booking")
