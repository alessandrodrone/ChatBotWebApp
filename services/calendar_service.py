"""
Servizio Calendario – integrazione Google Calendar API.
Gestisce: verifica disponibilità, creazione/cancellazione eventi,
ricerca prenotazioni cliente.

OTTIMIZZAZIONI vs vecchio monolite:
- Batch loading eventi per giorno (1 call API per operatore per giorno)
- Verifica slot con eventi pre-caricati (zero call aggiuntive)
- Riuso credenziali cachate
"""
from __future__ import annotations

import json
import logging
import uuid
import datetime as dt
from typing import Dict, List, Optional, Tuple

from flask import current_app

from utils.helpers import (
    norm_phone, norm_text, safe_lower, parse_int, now_utc, utc_now_iso,
    shop_tz,
)

log = logging.getLogger(__name__)

# Cache del client Calendar
_calendar_client = None

BLOCK_KEYWORDS = {"chiuso", "ferie", "malattia", "off", "closed", "vacation", "sick"}


# ── Inizializzazione client ──────────────────────────────────

def _get_calendar_client():
    """Restituisce il client Calendar API con cache."""
    global _calendar_client
    if _calendar_client is not None:
        return _calendar_client

    creds_json = current_app.config.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not creds_json:
        return None

    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        creds_dict = json.loads(creds_json)
        scopes = [
            "https://www.googleapis.com/auth/calendar",
        ]
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        _calendar_client = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        return _calendar_client
    except Exception:
        log.exception("Impossibile inizializzare Google Calendar client")
        return None


# ── Helpers interni ──────────────────────────────────────────

def _has_block_keyword(summary: str) -> bool:
    s = safe_lower(summary)
    return any(k in s for k in BLOCK_KEYWORDS)


def _load_day_events(calendar_id: str, day: dt.date, tz: dt.tzinfo) -> List[Dict]:
    """Carica TUTTI gli eventi di un giorno in una sola chiamata API."""
    cal = _get_calendar_client()
    if cal is None:
        return []

    start_of_day = dt.datetime.combine(day, dt.time.min, tzinfo=tz)
    end_of_day = dt.datetime.combine(day, dt.time.max, tzinfo=tz)
    try:
        evs = cal.events().list(
            calendarId=calendar_id,
            timeMin=start_of_day.isoformat(),
            timeMax=end_of_day.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
        ).execute().get("items", [])
        return evs
    except Exception as e:
        log.warning("load_day_events failed %s: %s", calendar_id, e)
        return []


def _slot_is_free_with_events(
    events: List[Dict], start: dt.datetime, end: dt.datetime
) -> bool:
    """Verifica se uno slot è libero usando eventi pre-caricati (zero API call)."""
    for ev in events:
        summary = ev.get("summary", "")
        transparency = ev.get("transparency", "")

        ev_start_str = (ev.get("start") or {}).get("dateTime")
        ev_end_str = (ev.get("end") or {}).get("dateTime")
        if not ev_start_str or not ev_end_str:
            continue
        try:
            ev_start = dt.datetime.fromisoformat(ev_start_str)
            ev_end = dt.datetime.fromisoformat(ev_end_str)
        except Exception:
            continue

        # Nessuna sovrapposizione → skip
        if ev_start >= end or ev_end <= start:
            continue

        # Keyword di blocco → occupato
        if _has_block_keyword(summary):
            return False

        # Evento opaco (non transparent) → occupato
        if transparency != "transparent":
            return False

    return True


def _find_free_operator_with_events(
    operators: List[Dict],
    events_by_cal: Dict[str, List[Dict]],
    start: dt.datetime,
    end: dt.datetime,
) -> Optional[Dict]:
    """Trova il primo operatore libero usando eventi pre-caricati."""
    for op in operators:
        cal_id = op.get("calendar_id")
        if not cal_id:
            continue
        events = events_by_cal.get(cal_id, [])
        if _slot_is_free_with_events(events, start, end):
            return op
    return None


def _count_concurrent_events(
    events_by_cal: Dict[str, List[Dict]], start: dt.datetime, end: dt.datetime
) -> int:
    """Conta eventi opachi sovrapposti a uno slot su TUTTI i calendari."""
    count = 0
    for events in events_by_cal.values():
        for ev in events:
            ev_start_str = (ev.get("start") or {}).get("dateTime")
            ev_end_str = (ev.get("end") or {}).get("dateTime")
            if not ev_start_str or not ev_end_str:
                continue
            try:
                ev_start = dt.datetime.fromisoformat(ev_start_str)
                ev_end = dt.datetime.fromisoformat(ev_end_str)
            except Exception:
                continue
            if ev_start < end and ev_end > start:
                if not _has_block_keyword(ev.get("summary", "")):
                    if ev.get("transparency") != "transparent":
                        count += 1
    return count


# ── API pubbliche ────────────────────────────────────────────

def load_hours_parsed(shop_id: str) -> Dict[int, List[Tuple[dt.time, dt.time]]]:
    """
    Carica e parsa gli orari dal foglio hours.
    Ritorna {weekday_int: [(start_time, end_time), ...]}
    con eventuale pausa che spezza l'intervallo in due.
    """
    from services.sheets_service import get_hours_for_shop

    out: Dict[int, List[Tuple[dt.time, dt.time]]] = {i: [] for i in range(7)}

    for r in get_hours_for_shop(shop_id):
        try:
            wd = int(r.get("weekday", "-1"))
        except (ValueError, TypeError):
            continue

        try:
            start_t = dt.time.fromisoformat(r.get("start", ""))
            end_t = dt.time.fromisoformat(r.get("end", ""))
        except (ValueError, TypeError):
            continue

        pause_start_raw = r.get("pause_start") or ""
        pause_end_raw = r.get("pause_end") or ""

        if pause_start_raw and pause_end_raw:
            try:
                ps = dt.time.fromisoformat(pause_start_raw)
                pe = dt.time.fromisoformat(pause_end_raw)
                if start_t < ps <= pe <= end_t:
                    if start_t < ps:
                        out[wd].append((start_t, ps))
                    if pe < end_t:
                        out[wd].append((pe, end_t))
                    continue
            except Exception:
                pass

        out[wd].append((start_t, end_t))

    return out


def list_free_slots_for_day(
    hours: Dict[int, List[Tuple[dt.time, dt.time]]],
    operators: List[Dict],
    day: dt.date,
    dur_min: int,
    slot_minutes: int,
    tz: dt.tzinfo,
    limit: int,
    events_by_cal: Optional[Dict[str, List[Dict]]] = None,
    *,
    max_concurrent: int = 0,
    all_operators: Optional[List[Dict]] = None,
) -> List[Tuple[dt.datetime, Dict]]:
    """
    Trova slot liberi per un giorno.
    max_concurrent: limite globale appuntamenti per slot (0 = no limite).
    all_operators: tutti gli operatori del negozio (per conteggio globale).
    """
    out: List[Tuple[dt.datetime, Dict]] = []
    ranges = hours.get(day.weekday(), []) or []

    # Pre-carica eventi
    if events_by_cal is None:
        events_by_cal = {}
        # Carica per gli operatori da cercare
        for op in operators:
            cal_id = op.get("calendar_id")
            if cal_id and cal_id not in events_by_cal:
                events_by_cal[cal_id] = _load_day_events(cal_id, day, tz)
        # Se max_concurrent, carica anche per tutti gli altri operatori
        if max_concurrent > 0 and all_operators:
            for op in all_operators:
                cal_id = op.get("calendar_id")
                if cal_id and cal_id not in events_by_cal:
                    events_by_cal[cal_id] = _load_day_events(cal_id, day, tz)

    for st, en in ranges:
        cur = dt.datetime.combine(day, st, tzinfo=tz)
        end_limit = dt.datetime.combine(day, en, tzinfo=tz)
        while cur + dt.timedelta(minutes=dur_min) <= end_limit:
            end_dt = cur + dt.timedelta(minutes=dur_min)
            op = _find_free_operator_with_events(operators, events_by_cal, cur, end_dt)
            if op:
                # Verifica limite globale
                if max_concurrent > 0:
                    total = _count_concurrent_events(events_by_cal, cur, end_dt)
                    if total >= max_concurrent:
                        cur += dt.timedelta(minutes=slot_minutes)
                        continue
                out.append((cur, op))
                if len(out) >= limit:
                    return out
            cur += dt.timedelta(minutes=slot_minutes)
    return out


def list_available_days(
    hours: Dict[int, List[Tuple[dt.time, dt.time]]],
    operators: List[Dict],
    start_day: dt.date,
    dur_min: int,
    slot_minutes: int,
    tz: dt.tzinfo,
    limit_days: int,
    *,
    max_concurrent: int = 0,
    all_operators: Optional[List[Dict]] = None,
) -> List[dt.date]:
    """
    Trova giorni con almeno uno slot libero.
    Ottimizzato: 1 call API per operatore per giorno.
    """
    max_ahead = current_app.config.get("MAX_LOOKAHEAD_DAYS", 30)
    days: List[dt.date] = []
    for off in range(max_ahead):
        day = start_day + dt.timedelta(days=off)
        if not hours.get(day.weekday(), []):
            continue

        events_by_cal: Dict[str, List[Dict]] = {}
        ops_to_load = list(operators)
        if max_concurrent > 0 and all_operators:
            ops_to_load = all_operators
        for op in ops_to_load:
            cal_id = op.get("calendar_id")
            if cal_id and cal_id not in events_by_cal:
                events_by_cal[cal_id] = _load_day_events(cal_id, day, tz)

        slots = list_free_slots_for_day(
            hours, operators, day, dur_min, slot_minutes,
            tz, limit=1, events_by_cal=events_by_cal,
            max_concurrent=max_concurrent, all_operators=all_operators,
        )
        if slots:
            days.append(day)
            if len(days) >= limit_days:
                return days
    return days


def find_free_operator_for_slot(
    operators: List[Dict], start: dt.datetime, end: dt.datetime, tz: dt.tzinfo
) -> Optional[Dict]:
    """Verifica in tempo reale quale operatore è libero per uno slot specifico."""
    day = start.date()
    events_by_cal: Dict[str, List[Dict]] = {}
    for op in operators:
        cal_id = op.get("calendar_id")
        if cal_id and cal_id not in events_by_cal:
            events_by_cal[cal_id] = _load_day_events(cal_id, day, tz)
    return _find_free_operator_with_events(operators, events_by_cal, start, end)


def create_booking_event(
    calendar_id: str,
    start: dt.datetime,
    end: dt.datetime,
    service_name: str,
    customer_name: str,
    customer_phone: str,
    shop_name: str,
    operator_name: str,
    booking_id: str,
    booking_key: str,
    *,
    summary_override: str = "",
    booking_notes: str = "",
) -> str:
    """Crea un evento di prenotazione su Google Calendar."""
    cal = _get_calendar_client()
    if cal is None:
        log.error("Calendar client non disponibile – evento non creato")
        return ""

    summary = summary_override or f"{customer_name} – {service_name}".strip(" –")
    desc_parts = [
        f"Attività: {shop_name}",
        f"Operatore: {operator_name}",
        "",
        f"Cliente: {customer_name}",
        f"Telefono: {norm_phone(customer_phone)}",
        f"Servizio: {service_name}",
    ]
    if booking_notes:
        desc_parts.append(f"Note: {booking_notes}")
    desc_parts.append("")
    desc_parts.append(f"Booking ID: {booking_id}")
    description = "\n".join(desc_parts)

    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "transparency": "opaque",
        "visibility": "private",
        "extendedProperties": {
            "private": {
                "booking_id": booking_id,
                "booking_key": booking_key,
                "customer_phone": norm_phone(customer_phone),
                "customer_name": customer_name,
                "service": service_name,
                "shop": shop_name,
                "operator": operator_name,
                "reminder_24h_sent": "0",
            }
        },
    }

    try:
        ev = cal.events().insert(calendarId=calendar_id, body=body).execute()
        log.info("Evento creato su %s: %s", calendar_id, ev.get("id", ""))
        return ev.get("id", "")
    except Exception:
        log.exception("Errore creazione evento su Calendar")
        return ""


def delete_event(calendar_id: str, event_id: str) -> bool:
    cal = _get_calendar_client()
    if cal is None:
        return False
    try:
        cal.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return True
    except Exception as e:
        log.warning("delete_event failed: %s", e)
        return False


def patch_event_private_props(calendar_id: str, event_id: str, updates: Dict[str, str]) -> bool:
    cal = _get_calendar_client()
    if cal is None:
        return False
    try:
        ev = cal.events().get(calendarId=calendar_id, eventId=event_id).execute()
        ep = ev.get("extendedProperties") or {}
        priv = ep.get("private") or {}
        priv = {**priv, **{k: str(v) for k, v in (updates or {}).items()}}
        body = {"extendedProperties": {"private": priv}}
        cal.events().patch(calendarId=calendar_id, eventId=event_id, body=body).execute()
        return True
    except Exception as e:
        log.warning("patch_event_private_props failed %s %s: %s", calendar_id, event_id, e)
        return False


def find_upcoming_customer_event(
    operators: List[Dict], customer_phone: str, tz: dt.tzinfo
) -> Optional[Tuple[str, str, Dict]]:
    """Cerca il prossimo evento futuro di un cliente (per disdetta/spostamento)."""
    cal = _get_calendar_client()
    if cal is None:
        return None

    phone = norm_phone(customer_phone)
    if not phone:
        return None

    lookahead = current_app.config.get("FUTURE_CANCEL_LOOKAHEAD_DAYS", 120)
    time_min = dt.datetime.now(tz).replace(microsecond=0).isoformat()
    time_max = (dt.datetime.now(tz) + dt.timedelta(days=lookahead)).replace(microsecond=0).isoformat()

    best: Optional[Tuple[dt.datetime, str, str, Dict]] = None

    for op in operators:
        cal_id = op.get("calendar_id")
        if not cal_id:
            continue
        try:
            evs = cal.events().list(
                calendarId=cal_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            ).execute().get("items", [])
        except Exception as e:
            log.warning("list events failed %s: %s", cal_id, e)
            continue

        for ev in evs:
            ep = ((ev.get("extendedProperties") or {}).get("private") or {})
            ev_phone = norm_phone(ep.get("customer_phone", ""))
            if ev_phone != phone:
                continue
            st = (ev.get("start") or {}).get("dateTime")
            if not st:
                continue
            try:
                st_dt = dt.datetime.fromisoformat(st)
            except Exception:
                continue
            if best is None or st_dt < best[0]:
                best = (st_dt, cal_id, ev.get("id", ""), ev)

    if not best:
        return None
    return best[1], best[2], best[3]


def can_change_booking(ev: Dict, tz: dt.tzinfo) -> bool:
    """Permette disdetta/spostamento SOLO fino a 24 ore prima."""
    st = (ev.get("start") or {}).get("dateTime")
    if not st:
        return False
    try:
        start_dt = dt.datetime.fromisoformat(st)
    except Exception:
        return False
    now_local = dt.datetime.now(tz)
    start_local = start_dt.astimezone(tz)
    return (start_local - now_local) >= dt.timedelta(hours=24)


def booking_key(shop_id: str, customer_phone: str, service_name: str, start: dt.datetime) -> str:
    raw = f"{shop_id}|{norm_phone(customer_phone)}|{service_name}|{start.isoformat()}"
    return uuid.uuid5(uuid.NAMESPACE_URL, raw).hex


def _event_dt(ev: Dict, tz: dt.tzinfo) -> Optional[dt.datetime]:
    st = (ev.get("start") or {}).get("dateTime")
    if not st:
        return None
    try:
        return dt.datetime.fromisoformat(st).astimezone(tz)
    except Exception:
        return None
