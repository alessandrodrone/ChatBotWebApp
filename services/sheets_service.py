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
_operators_cache: dict | None = None  # {shop_id: [operator_dict, ...]}
_hours_cache: dict | None = None  # {shop_id: [hour_dict, ...]}
_services_cache: dict | None = None  # {shop_id: [service_dict, ...]}
_customers_cache: dict | None = None  # {phone: customer_dict}


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
            # Il foglio usa "shop_id" come colonna chiave
            sid = str(row.get("shop_id", "")).strip()
            if sid:
                _shops_cache[sid] = {
                    "id": sid,
                    "name": row.get("name", ""),
                    "whatsapp_number": str(row.get("whatsapp_number", "")),
                    "phone": str(row.get("whatsapp_number", "")),
                    "phone_number_id": str(row.get("phone_number_id", "")),
                    "owner_phone": str(row.get("owner_phone", "")),
                    "timezone": row.get("timezone", "Europe/Rome"),
                    "slot_minutes": int(row.get("slot_minutes", 30) or 30),
                    "info": row.get("info", ""),
                    # Campi opzionali per la landing page
                    "address": row.get("address", ""),
                    "description": row.get("description", ""),
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


def get_all_operators() -> dict:
    """
    Legge il foglio 'operators' e restituisce un dict:
    {shop_id: [operator_dict, ...]}

    Colonne attese: shop_id, operator_id, operator_name,
    calendar_id, active, priority, skills, gender
    """
    global _operators_cache
    if _operators_cache is not None:
        return _operators_cache

    client = _get_gspread_client()
    if client is None:
        _operators_cache = {}
        return _operators_cache

    try:
        spreadsheet_id = current_app.config["GOOGLE_SHEET_ID"]
        sheet = client.open_by_key(spreadsheet_id).worksheet("operators")
        records = sheet.get_all_records()
        _operators_cache = {}
        for row in records:
            sid = str(row.get("shop_id", "")).strip()
            if not sid:
                continue
            active_val = str(row.get("active", "TRUE")).strip().upper()
            if active_val in {"FALSE", "0", "NO", "N"}:
                continue  # Salta operatori disattivati
            op = {
                "operator_id": str(row.get("operator_id", "")).strip(),
                "operator_name": row.get("operator_name", ""),
                "calendar_id": str(row.get("calendar_id", "")).strip(),
                "active": True,
                "priority": int(row.get("priority", 0) or 0),
                "skills": row.get("skills", ""),
                "gender": row.get("gender", ""),
            }
            _operators_cache.setdefault(sid, []).append(op)

        # Ordina per priorità (più alta prima)
        for sid in _operators_cache:
            _operators_cache[sid].sort(key=lambda o: o["priority"], reverse=True)

        return _operators_cache
    except Exception:
        log.exception("Errore lettura operators da Google Sheets")
        _operators_cache = {}
        return _operators_cache


def get_operators_for_shop(shop_id: str) -> list[dict]:
    """Restituisce la lista di operatori attivi per uno shop."""
    operators = get_all_operators()
    return operators.get(shop_id, [])


def get_all_hours() -> dict:
    """
    Legge il foglio 'hours' e restituisce un dict:
    {shop_id: [hour_dict, ...]}

    Colonne attese: shop_id, weekday, start, end, pause-start, pause-end
    """
    global _hours_cache
    if _hours_cache is not None:
        return _hours_cache

    client = _get_gspread_client()
    if client is None:
        _hours_cache = {}
        return _hours_cache

    try:
        spreadsheet_id = current_app.config["GOOGLE_SHEET_ID"]
        sheet = client.open_by_key(spreadsheet_id).worksheet("hours")
        records = sheet.get_all_records()
        _hours_cache = {}
        for row in records:
            sid = str(row.get("shop_id", "")).strip()
            if not sid:
                continue
            hour = {
                "weekday": str(row.get("weekday", "")).strip(),
                "start": str(row.get("start", "")).strip(),
                "end": str(row.get("end", "")).strip(),
                "pause_start": str(row.get("pause-start", "")).strip(),
                "pause_end": str(row.get("pause-end", "")).strip(),
            }
            _hours_cache.setdefault(sid, []).append(hour)

        return _hours_cache
    except Exception:
        log.exception("Errore lettura hours da Google Sheets")
        _hours_cache = {}
        return _hours_cache


def get_hours_for_shop(shop_id: str) -> list[dict]:
    """Restituisce gli orari settimanali di uno shop."""
    hours = get_all_hours()
    return hours.get(shop_id, [])


def get_all_services() -> dict:
    """
    Legge il foglio 'services' e restituisce un dict:
    {shop_id: [service_dict, ...]}

    Colonne attese: shop_id, name, duration, price, category, active
    """
    global _services_cache
    if _services_cache is not None:
        return _services_cache

    client = _get_gspread_client()
    if client is None:
        _services_cache = {}
        return _services_cache

    try:
        spreadsheet_id = current_app.config["GOOGLE_SHEET_ID"]
        sheet = client.open_by_key(spreadsheet_id).worksheet("services")
        records = sheet.get_all_records()
        _services_cache = {}
        for row in records:
            sid = str(row.get("shop_id", "")).strip()
            if not sid:
                continue
            active_val = str(row.get("active", "TRUE")).strip().upper()
            if active_val in {"FALSE", "0", "NO", "N"}:
                continue  # Salta servizi disattivati
            svc = {
                "name": row.get("name", ""),
                "duration": int(row.get("duration", 30) or 30),
                "price": str(row.get("price", "")),
                "category": row.get("category", ""),
                "active": True,
            }
            _services_cache.setdefault(sid, []).append(svc)

        return _services_cache
    except Exception:
        log.exception("Errore lettura services da Google Sheets")
        _services_cache = {}
        return _services_cache


def get_services_for_shop(shop_id: str) -> list[dict]:
    """Restituisce la lista dei servizi attivi per uno shop."""
    services = get_all_services()
    return services.get(shop_id, [])


def get_all_customers() -> dict:
    """
    Legge il foglio 'customers' e restituisce un dict:
    {phone: customer_dict}

    Colonne attese: shop_id, phone, last_service, total_visits,
    last_visit, customer_name, last_seen_phone_number_id, updated_at
    """
    global _customers_cache
    if _customers_cache is not None:
        return _customers_cache

    client = _get_gspread_client()
    if client is None:
        _customers_cache = {}
        return _customers_cache

    try:
        spreadsheet_id = current_app.config["GOOGLE_SHEET_ID"]
        tab_name = current_app.config.get("CUSTOMERS_TAB", "customers")
        sheet = client.open_by_key(spreadsheet_id).worksheet(tab_name)
        records = sheet.get_all_records()
        _customers_cache = {}
        for row in records:
            phone = str(row.get("phone", "")).strip()
            if not phone:
                continue
            _customers_cache[phone] = {
                "phone": phone,
                "shop_id": str(row.get("shop_id", "")).strip(),
                "customer_name": row.get("customer_name", ""),
                "last_service": row.get("last_service", ""),
                "total_visits": int(row.get("total_visits", 0) or 0),
                "last_visit": str(row.get("last_visit", "")),
                "last_seen_phone_number_id": str(row.get("last_seen_phone_number_id", "")),
                "updated_at": str(row.get("updated_at", "")),
            }
        return _customers_cache
    except Exception:
        log.exception("Errore lettura customers da Google Sheets")
        _customers_cache = {}
        return _customers_cache


def get_customer_by_phone(phone: str) -> dict | None:
    """Restituisce il record di un cliente per numero di telefono."""
    customers = get_all_customers()
    return customers.get(phone)


def upsert_customer_to_sheet(
    phone: str,
    shop_id: str,
    customer_name: str = "",
    last_seen_phone_number_id: str = "",
):
    """
    Crea o aggiorna un cliente sul foglio 'customers'.
    Cerca per numero di telefono; se esiste aggiorna, altrimenti appende.
    """
    from datetime import datetime, timezone

    client = _get_gspread_client()
    if client is None:
        log.warning("Google Sheets non configurato – customer non salvato.")
        return

    try:
        spreadsheet_id = current_app.config["GOOGLE_SHEET_ID"]
        tab_name = current_app.config.get("CUSTOMERS_TAB", "customers")
        sheet = client.open_by_key(spreadsheet_id).worksheet(tab_name)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Cerca se il cliente esiste già (colonna B = phone)
        try:
            cell = sheet.find(phone, in_column=2)
            row_num = cell.row
            # Aggiorna i campi
            sheet.update_cell(row_num, 1, shop_id)  # shop_id
            if customer_name:
                sheet.update_cell(row_num, 6, customer_name)  # customer_name
            if last_seen_phone_number_id:
                sheet.update_cell(row_num, 7, last_seen_phone_number_id)
            sheet.update_cell(row_num, 8, now)  # updated_at
            log.info("Customer %s aggiornato su sheet (riga %s)", phone, row_num)
        except Exception:
            # Non trovato → appendi nuova riga
            new_row = [
                shop_id,
                phone,
                "",  # last_service
                0,   # total_visits
                "",  # last_visit
                customer_name,
                last_seen_phone_number_id,
                now,
            ]
            sheet.append_row(new_row, value_input_option="USER_ENTERED")
            log.info("Customer %s aggiunto su sheet", phone)

        # Invalida la cache customers
        global _customers_cache
        _customers_cache = None

    except Exception:
        log.exception("Errore scrittura customer su Google Sheets")


def invalidate_shops_cache():
    """Invalida la cache – utile dopo un aggiornamento del foglio."""
    global _shops_cache, _operators_cache, _hours_cache, _services_cache, _customers_cache
    _shops_cache = None
    _operators_cache = None
    _hours_cache = None
    _services_cache = None
    _customers_cache = None
