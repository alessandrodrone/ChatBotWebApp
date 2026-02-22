"""
Funzioni helper generiche.
"""
from __future__ import annotations

import re
import datetime as dt
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


# ── Testo / telefono ──────────────────────────────────────────

def norm_phone(phone: str) -> str:
    """Rimuove tutti i caratteri non-digit da un telefono."""
    return re.sub(r"\D+", "", phone or "")


sanitize_phone = norm_phone  # alias legacy


def norm_text(v: str) -> str:
    return (v or "").strip()


def safe_lower(v: str) -> str:
    return norm_text(v).lower()


def truncate(text: str, max_len: int = 100) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


# ── Parsing ───────────────────────────────────────────────────

def parse_bool(v: str) -> bool:
    return str(v).strip().lower() in {"true", "1", "yes", "y", "si", "sì"}


def parse_int(v: str, default: int) -> int:
    try:
        return int(str(v).strip())
    except Exception:
        return default


# ── Data / ora ────────────────────────────────────────────────

def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def utc_now_iso() -> str:
    return now_utc().replace(microsecond=0).isoformat()


def parse_iso_dt(s: str) -> Optional[dt.datetime]:
    try:
        return dt.datetime.fromisoformat(s)
    except Exception:
        return None


def shop_tz(shop: dict) -> dt.tzinfo:
    """Restituisce il tzinfo del negozio (da colonna timezone)."""
    tz_name = norm_text(shop.get("timezone")) or "Europe/Rome"
    if ZoneInfo:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            return dt.timezone.utc
    return dt.timezone.utc


# ── SHOP=... hint ─────────────────────────────────────────────

def extract_shop_hint(text: str) -> Optional[str]:
    m = re.search(r"\bSHOP\s*=\s*([A-Za-z0-9_\-]+)\b", text or "", flags=re.I)
    return m.group(1) if m else None


def strip_shop_hint(text: str) -> str:
    return re.sub(r"\bSHOP\s*=\s*[A-Za-z0-9_\-]+\b", "", text or "", flags=re.I).strip()


# ── Nomi giorni IT ────────────────────────────────────────────

WEEKDAYS_IT = [
    "lunedì", "martedì", "mercoledì", "giovedì",
    "venerdì", "sabato", "domenica",
]
