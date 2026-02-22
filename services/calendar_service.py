"""
Servizio Calendario – placeholder per integrazione Google Calendar.
"""

import logging

log = logging.getLogger(__name__)


def get_available_slots(shop_id: str, date_str: str) -> list[str]:
    """Restituisce gli slot disponibili per una data (placeholder)."""
    # TODO: integrare con Google Calendar API
    return ["09:00", "10:00", "11:00", "14:00", "15:00", "16:00"]


def book_slot(shop_id: str, date_str: str, time_str: str, customer_phone: str) -> bool:
    """Prenota uno slot (placeholder)."""
    log.info("Prenotazione: shop=%s data=%s ora=%s tel=%s", shop_id, date_str, time_str, customer_phone)
    return True
