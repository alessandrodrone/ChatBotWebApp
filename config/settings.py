"""
Configurazione centralizzata – legge variabili d'ambiente.
Su Railway imposti queste env var nel pannello Settings > Variables.
"""

import os


def _env(primary: str, *fallbacks: str, default: str = "") -> str:
    """Cerca la variabile con il nome primario, poi i fallback."""
    val = os.environ.get(primary)
    if val:
        return val
    for fb in fallbacks:
        val = os.environ.get(fb)
        if val:
            return val
    return default


def _env_bool(primary: str, default: str = "false") -> bool:
    return _env(primary, default=default).strip().lower() in {"1", "true", "yes", "y", "si", "sì"}


class Config:
    # ── Meta / WhatsApp Cloud API ─────────────────────────────
    META_ACCESS_TOKEN = _env("META_ACCESS_TOKEN", "WHATSAPP_TOKEN")
    META_PHONE_NUMBER_ID = _env("META_PHONE_NUMBER_ID", "PHONE_NUMBER_ID", "NUMERO_DI_TELEFONO", "WHATSAPP_PHONE_NUMBER_ID")
    META_VERIFY_TOKEN = _env("META_VERIFY_TOKEN", "VERIFY_TOKEN", default="risponditu_verify_2026")
    META_APP_SECRET = _env("META_APP_SECRET", "META_API_SECRET")
    GRAPH_API_VERSION = os.environ.get("GRAPH_API_VERSION", "v20.0")
    WHATSAPP_NUMBER = os.environ.get("WHATSAPP_NUMBER", "391234567890")

    # ── Google Sheets ─────────────────────────────────────────
    GOOGLE_SERVICE_ACCOUNT_JSON = _env("GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_SHEETS_CREDENTIALS_JSON")
    GOOGLE_SHEET_ID = _env("GOOGLE_SHEET_ID", "SPREADSHEET_ID")
    CUSTOMERS_TAB = os.environ.get("CUSTOMERS_TAB", "customers")

    # ── Sessione & Agenda ─────────────────────────────────────
    SESSION_TTL_MINUTES = int(os.environ.get("SESSION_TTL_MINUTES", "45"))
    MAX_LOOKAHEAD_DAYS = int(os.environ.get("MAX_LOOKAHEAD_DAYS", "30"))
    DEFAULT_SLOT_MINUTES = int(os.environ.get("DEFAULT_SLOT_MINUTES", "30"))
    DEFAULT_MIN_ADVANCE_HOURS = int(os.environ.get("DEFAULT_MIN_ADVANCE_HOURS", "0"))
    CUSTOMER_SHOP_TTL_DAYS = int(os.environ.get("CUSTOMER_SHOP_TTL_DAYS", "0"))  # 0 = per sempre

    # ── Blocchi agenda ────────────────────────────────────────
    BLOCK_KEYWORDS = {"chiuso", "ferie", "malattia", "off", "closed", "vacation", "sick"}

    # ── Opzioni UI ────────────────────────────────────────────
    MAX_DAY_OPTIONS = int(os.environ.get("MAX_DAY_OPTIONS", "30"))
    MAX_TIME_OPTIONS = int(os.environ.get("MAX_TIME_OPTIONS", "48"))

    # ── Cancellazione ─────────────────────────────────────────
    FUTURE_CANCEL_LOOKAHEAD_DAYS = int(os.environ.get("FUTURE_CANCEL_LOOKAHEAD_DAYS", "120"))

    # ── Notifiche & Debug ─────────────────────────────────────
    STORE_CUSTOMER_DEBUG_FIELDS = _env_bool("STORE_CUSTOMER_DEBUG_FIELDS", default="true")
    ENABLE_OWNER_NOTIFY = _env_bool("ENABLE_OWNER_NOTIFY", default="true")

    # ── Cron / Promemoria ─────────────────────────────────────
    CRON_TOKEN = os.environ.get("CRON_TOKEN", "")
    REMINDER_WINDOW_MINUTES = int(os.environ.get("REMINDER_WINDOW_MINUTES", "60"))

    # ── Flask ─────────────────────────────────────────────────
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")

    # ── Demo shops (usati quando non c'è Google Sheet) ────────
    DEMO_SHOPS = {
        "demo1": {
            "id": "demo1",
            "name": "Barbiere Da Mario",
            "address": "Via Roma 42, Milano",
            "description": "Taglio classico e moderno dal 1985.",
            "phone": "391234567890",
            "color": "#1a1a2e",
            "accent": "#e94560",
        },
        "demo2": {
            "id": "demo2",
            "name": "Hair Studio Luca",
            "address": "Corso Italia 18, Torino",
            "description": "Il tuo look, la nostra passione.",
            "phone": "391234567890",
            "color": "#0f3460",
            "accent": "#16c79a",
        },
        "demo3": {
            "id": "demo3",
            "name": "Salone Eleganza",
            "address": "Piazza Duomo 7, Roma",
            "description": "Stile e cura per l'uomo moderno.",
            "phone": "391234567890",
            "color": "#2b2d42",
            "accent": "#ef233c",
        },
    }
