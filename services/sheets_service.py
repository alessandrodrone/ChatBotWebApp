"""
Servizio Google Sheets – recupera i dati degli shop.

Se le credenziali Google non sono configurate, usa i DEMO_SHOPS
definiti in config/settings.py (utile per test locale).
"""
from __future__ import annotations
import json
import logging
from flask import current_app

log = logging.getLogger(__name__)

# Cache in memoria (opzionale, evita chiamate ripetute)
_sheets_client = None
_shops_cache: dict | None = None


def _get_gspread_client():
    """Inizializza il client gspread con le credenziali JSON."""
    global _sheets_client
    if _sheets_client:
        return _sheets_client

    creds_json = current_app.config.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not creds_json:
        return None

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds_dict = json.loads(creds_json)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        _sheets_client = gspread.authorize(credentials)
        return _sheets_client
    except Exception:
        log.exception("Impossibile inizializzare gspread")
        return None


def get_all_shops() -> dict:
    """Restituisce tutti gli shop come dict {shop_id: shop_data}."""
    global _shops_cache
    if _shops_cache is not None:
        return _shops_cache

    client = _get_gspread_client()
    if client is None:
        # Fallback → demo shops per sviluppo locale
        log.info("Google Sheets non configurato – uso DEMO_SHOPS")
        _shops_cache = current_app.config.get("DEMO_SHOPS", {})
        return _shops_cache

    try:
        spreadsheet_id = current_app.config["GOOGLE_SHEET_ID"]
        sheet = client.open_by_key(spreadsheet_id).worksheet("shops")
        records = sheet.get_all_records()
        _shops_cache = {}
        for row in records:
            sid = str(row.get("id", "")).strip()
            if sid:
                _shops_cache[sid] = {
                    "id": sid,
                    "name": row.get("name", ""),
                    "address": row.get("address", ""),
                    "description": row.get("description", ""),
                    "phone": str(row.get("phone", "")),
                    "color": row.get("color", "#1a1a2e"),
                    "accent": row.get("accent", "#e94560"),
                }
        return _shops_cache
    except Exception:
        log.exception("Errore lettura shops da Google Sheets")
        _shops_cache = current_app.config.get("DEMO_SHOPS", {})
        return _shops_cache


def get_shop_by_id(shop_id: str) -> dict | None:
    """Restituisce un singolo shop per ID, oppure None."""
    shops = get_all_shops()
    return shops.get(shop_id)


def invalidate_shops_cache():
    """Invalida la cache – utile dopo un aggiornamento del foglio."""
    global _shops_cache
    _shops_cache = None
