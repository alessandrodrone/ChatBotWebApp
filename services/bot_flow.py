"""
Bot Flow – logica conversazionale completa.
Porta di peso tutto il flusso dal vecchio monolite, modularizzato.

Flusso: WELCOME → SERVICES → OPERATOR → PERIOD → DAY_SELECT →
        TIME_RANGE → TIME_SELECT → CONFIRM
+ Gestione disdetta/spostamento con policy 24h
"""
from __future__ import annotations

import logging
import uuid
import datetime as dt
from typing import Dict, List, Optional, Tuple

from flask import current_app

from services.whatsapp_service import (
    send_text_message,
    send_interactive_buttons,
    send_list_message,
    notify_owner,
)
from services.session_service import get_session, save_session, clear_session
from services.sheets_service import (
    get_services_for_shop,
    get_operators_for_shop,
)
from services.calendar_service import (
    load_hours_parsed,
    list_available_days,
    list_free_slots_for_day,
    find_free_operator_for_slot,
    create_booking_event,
    delete_event,
    find_upcoming_customer_event,
    can_change_booking,
    booking_key,
)
from utils.helpers import (
    norm_phone, norm_text, safe_lower, parse_int, parse_bool,
    shop_tz, WEEKDAYS_IT,
)

log = logging.getLogger(__name__)

MAX_TIME_OPTIONS = 48  # default, sovrascritto da config


# ══════════════════════════════════════════════════════════════
# MENU HELPERS
# ══════════════════════════════════════════════════════════════

def _send_services_menu(from_phone, shop, services, pid, picked_ids=None):
    picked_ids = picked_ids or []
    rows = []
    for i, s in enumerate(services, 1):
        sid = f"svc_{i}"
        if sid in picked_ids:
            continue
        rows.append((sid, s.get("name", f"Servizio {i}"), ""))
    if not rows:
        send_text_message(from_phone, "Hai già selezionato tutti i servizi disponibili.", pid)
        return
    send_list_message(from_phone, "Scegli un servizio:", "Servizi", rows, pid)


def _send_days_menu(from_phone, days, pid):
    rows = []
    for d in days:
        rid = f"day_{d.isoformat()}"
        wd = WEEKDAYS_IT[d.weekday()]
        title = f"{wd} {d.strftime('%d/%m')}"
        rows.append((rid, title, ""))
    send_list_message(from_phone, "Scegli il giorno:", "Giorni", rows, pid)


def _send_times_menu(from_phone, slots, pid, show_operator=True):
    max_opts = current_app.config.get("MAX_TIME_OPTIONS", MAX_TIME_OPTIONS)
    if not slots:
        send_text_message(from_phone, "Nessun orario libero per questo giorno.", pid)
        send_interactive_buttons(
            from_phone, "Cosa vuoi fare?",
            [{"id": "ACT_BACK_DAY", "title": "Cambia giorno"}], pid,
        )
        return

    rows = []
    for st, op in slots[:max_opts]:
        rid = f"slot_{st.isoformat()}"
        title = st.strftime("%H:%M")
        desc = f"con {op.get('operator_name', 'Operatore')}" if show_operator else ""
        rows.append((rid, title, desc))
    send_list_message(from_phone, "Scegli l'orario:", "Orari", rows, pid)
    send_interactive_buttons(
        from_phone, "Se non trovi l'orario, cambia giorno:",
        [{"id": "ACT_BACK_DAY", "title": "Cambia giorno"}], pid,
    )


def _send_period_buttons(from_phone, pid):
    buttons = [
        {"id": "period_0_10", "title": "📆 Prossimi 10 gg"},
        {"id": "period_10_20", "title": "📆 Da 10 a 20 gg"},
        {"id": "period_20_30", "title": "📆 Da 20 a 30 gg"},
    ]
    send_interactive_buttons(from_phone, "Quando preferisci venire?", buttons, pid)


# ══════════════════════════════════════════════════════════════
# HANDLE BOT – entry point
# ══════════════════════════════════════════════════════════════

def handle_bot(
    shop: dict,
    from_phone: str,
    contact_name: Optional[str],
    incoming_text: str,
    interactive_id: Optional[str],
    phone_number_id: str,
) -> None:
    shop_id = shop.get("id", "")
    key = f"{shop_id}:{norm_phone(from_phone)}"
    tz = shop_tz(shop)

    services = get_services_for_shop(shop_id)
    hours = load_hours_parsed(shop_id)
    operators = get_operators_for_shop(shop_id)
    slot_minutes = parse_int(str(shop.get("slot_minutes", "")), current_app.config.get("DEFAULT_SLOT_MINUTES", 30))

    sess = get_session(key)
    if not sess:
        sess = {
            "state": "WELCOME",
            "picked": [],
            "picked_names": [],
            "picked_total_min": 0,
            "auto_assign_operator": False,
        }
    if contact_name:
        sess["customer_name"] = contact_name
    low = safe_lower(incoming_text)

    # ══════════════════════════════════════════════════════════
    # CANCEL / RESCHEDULE (globale, intercettato prima dello state)
    # ══════════════════════════════════════════════════════════
    if interactive_id in {"ACT_CANCEL", "ACT_RESCHEDULE"} or "disd" in low or "sposta" in low:
        found = find_upcoming_customer_event(operators, from_phone, tz)
        if not found:
            send_text_message(from_phone, "Non vedo appuntamenti futuri.", phone_number_id)
            return
        cal_id, ev_id, ev = found
        if not can_change_booking(ev, tz):
            owner_phone = norm_phone(shop.get("owner_phone", "") or "") or shop.get("name", "il negozio")
            send_text_message(
                from_phone,
                f"⚠️ Mancano meno di 24 ore al tuo appuntamento.\n"
                f"Per modifiche, contatta il negozio: {owner_phone}",
                phone_number_id,
            )
            notify_owner(
                shop,
                f"⚠️ Cliente {contact_name or from_phone} ha tentato modifica <24h.",
                phone_number_id,
            )
            return

        if interactive_id == "ACT_CANCEL" or "disd" in low:
            delete_event(cal_id, ev_id)
            clear_session(key)
            send_text_message(from_phone, "❌ Appuntamento annullato correttamente.", phone_number_id)
            notify_owner(shop, f"❌ Annullato\nCliente: {contact_name or from_phone}", phone_number_id)
            return

        if interactive_id == "ACT_RESCHEDULE" or "sposta" in low:
            sess["reschedule_target"] = {"calendar_id": cal_id, "event_id": ev_id}
            # Ripristina servizio e durata dall'evento
            ev_priv = ((ev.get("extendedProperties") or {}).get("private") or {})
            svc_name = norm_text(ev_priv.get("service") or ev.get("summary") or "")
            if "–" in svc_name:
                try:
                    svc_name = norm_text(svc_name.split("–", 1)[1])
                except Exception:
                    pass
            if not svc_name:
                svc_name = "Servizio"

            # Durata dall'evento
            st_str = (ev.get("start") or {}).get("dateTime")
            en_str = (ev.get("end") or {}).get("dateTime")
            dur_min = slot_minutes
            try:
                if st_str and en_str:
                    st_dt = dt.datetime.fromisoformat(st_str)
                    en_dt = dt.datetime.fromisoformat(en_str)
                    dur_min = max(1, int((en_dt - st_dt).total_seconds() // 60))
            except Exception:
                pass

            # Prova ad agganciare l'operatore dell'evento esistente
            ev_op_name = ev_priv.get("operator", "")
            picked_idx = None
            if ev_op_name:
                for i, op in enumerate(operators):
                    if safe_lower(op.get("operator_name", "")) == safe_lower(ev_op_name):
                        picked_idx = i
                        break
            if picked_idx is None:
                for i, op in enumerate(operators):
                    if norm_text(op.get("calendar_id")) == norm_text(cal_id):
                        picked_idx = i
                        break
            if picked_idx is not None:
                sess["picked_operator"] = picked_idx

            sess["picked"] = []
            sess["picked_names"] = [svc_name]
            sess["picked_total_min"] = dur_min
            sess["state"] = "PERIOD"
            save_session(key, sess)
            _send_period_buttons(from_phone, phone_number_id)
            return

    # ══════════════════════════════════════════════════════════
    # STATE MACHINE
    # ══════════════════════════════════════════════════════════
    state = sess.get("state", "WELCOME")

    # ── WELCOME ──────────────────────────────────────────────
    if state == "WELCOME":
        if not interactive_id:
            buttons = [
                {"id": "ACT_BOOK", "title": "Prenota appuntamento"},
                {"id": "ACT_MANAGE", "title": "Gestisci appuntamento"},
                {"id": "ACT_INFO", "title": "ℹ️ Info negozio"},
            ]
            send_interactive_buttons(
                from_phone,
                f"👋 Benvenuto da {shop.get('name', 'il nostro salone')}\n"
                "Posso aiutarti a prenotare un appuntamento in pochi secondi.\n"
                "Da dove vuoi iniziare?",
                buttons, phone_number_id,
            )
            save_session(key, sess)
            return

        if interactive_id == "ACT_BOOK":
            sess["state"] = "SERVICES"
            save_session(key, sess)
            _send_services_menu(from_phone, shop, services, phone_number_id, sess.get("picked"))
            return

        if interactive_id == "ACT_MANAGE":
            found = find_upcoming_customer_event(operators, from_phone, tz)
            if found:
                cal_id, ev_id, ev = found
                st = (ev.get("start") or {}).get("dateTime")
                when_txt = ""
                if st:
                    try:
                        when_txt = dt.datetime.fromisoformat(st).astimezone(tz).strftime("%d/%m/%Y %H:%M")
                    except Exception:
                        pass
                ep = (ev.get("extendedProperties") or {}).get("private") or {}
                op_name = ep.get("operator", "Operatore")
                svc = ep.get("service", "Servizio")
                send_text_message(
                    from_phone,
                    f"📝 Hai un appuntamento:\n• Servizio: {svc}\n• Quando: {when_txt}\n• Operatore: {op_name}",
                    phone_number_id,
                )
                send_interactive_buttons(
                    from_phone, "Cosa vuoi fare?",
                    [{"id": "ACT_RESCHEDULE", "title": "Sposta"}, {"id": "ACT_CANCEL", "title": "❌ Disdici"}],
                    phone_number_id,
                )
                return
            else:
                send_text_message(from_phone, "Non hai appuntamenti futuri.", phone_number_id)
                sess["state"] = "WELCOME"
                save_session(key, sess)
                return

        if interactive_id == "ACT_INFO":
            info = norm_text(shop.get("info")) or "Qui puoi inserire informazioni sul negozio."
            send_text_message(from_phone, info, phone_number_id)
            sess["state"] = "WELCOME"
            save_session(key, sess)
            return

    # ── Back to period ───────────────────────────────────────
    if interactive_id == "ACT_BACK_DAY":
        sess["state"] = "PERIOD"
        save_session(key, sess)
        _send_period_buttons(from_phone, phone_number_id)
        return

    # ── SERVICES ─────────────────────────────────────────────
    if state == "SERVICES":
        if interactive_id == "ACT_ADD":
            sess["state"] = "SERVICES"
            save_session(key, sess)
            _send_services_menu(from_phone, shop, services, phone_number_id, sess.get("picked"))
            return

        if interactive_id == "ACT_CHANGE":
            sess["picked"] = []
            sess["picked_names"] = []
            sess["picked_total_min"] = 0
            sess["state"] = "SERVICES"
            save_session(key, sess)
            send_text_message(from_phone, "Ok, scegli di nuovo il servizio:", phone_number_id)
            _send_services_menu(from_phone, shop, services, phone_number_id)
            return

        if interactive_id == "ACT_NEXT":
            if not sess.get("picked_names"):
                send_text_message(from_phone, "Prima scegli almeno un servizio.", phone_number_id)
                _send_services_menu(from_phone, shop, services, phone_number_id, sess.get("picked"))
                return

            if not operators:
                send_text_message(
                    from_phone,
                    "Al momento non ci sono operatori configurati. Contatta direttamente il negozio.",
                    phone_number_id,
                )
                sess["state"] = "WELCOME"
                save_session(key, sess)
                return

            # Controlla se ci sono operatori attivi per scelta utente
            active_ops = [op for op in operators if op.get("active", True)]
            if active_ops and len(active_ops) > 1:
                sess["state"] = "OPERATOR"
                save_session(key, sess)
                op_buttons = [
                    {"id": f"op_{i}", "title": f"👤 {op['operator_name']}"[:20]}
                    for i, op in enumerate(operators)
                ]
                op_buttons.append({"id": "op_any", "title": "👤 Chiunque"})
                # Max 3 bottoni → se > 3, usa lista
                if len(op_buttons) <= 3:
                    send_interactive_buttons(from_phone, "Con chi preferisci?", op_buttons, phone_number_id)
                else:
                    rows = [(b["id"], b["title"], "") for b in op_buttons]
                    send_list_message(from_phone, "Con chi preferisci?", "Operatori", rows, phone_number_id)
                return
            elif active_ops and len(active_ops) == 1:
                sess["picked_operator"] = 0
            else:
                sess["picked_operator"] = None
                sess["auto_assign_operator"] = True

            sess["state"] = "PERIOD"
            save_session(key, sess)
            _send_period_buttons(from_phone, phone_number_id)
            return

        # Selezione servizio dalla lista
        if interactive_id and interactive_id.startswith("svc_"):
            try:
                idx = int(interactive_id.split("_")[1])
            except (ValueError, IndexError):
                return
            if idx < 1 or idx > len(services):
                send_text_message(from_phone, "Servizio non valido.", phone_number_id)
                return

            svc = services[idx - 1]
            sess["picked"].append(interactive_id)
            sess["picked_names"].append(svc.get("name"))
            sess["picked_total_min"] += int(svc.get("duration", 30))
            save_session(key, sess)

            send_interactive_buttons(
                from_phone,
                f"Hai scelto *{svc.get('name')}*\nCosa vuoi fare?",
                [
                    {"id": "ACT_ADD", "title": "Aggiungi"},
                    {"id": "ACT_CHANGE", "title": "Cambia"},
                    {"id": "ACT_NEXT", "title": "Prosegui"},
                ],
                phone_number_id,
            )
            return

        _send_services_menu(from_phone, shop, services, phone_number_id, sess.get("picked"))
        return

    # ── OPERATOR ─────────────────────────────────────────────
    if state == "OPERATOR":
        if interactive_id and interactive_id.startswith("op_"):
            if interactive_id == "op_any":
                sess["picked_operator"] = None
            else:
                try:
                    idx = int(interactive_id.split("_")[1])
                except (ValueError, IndexError):
                    return
                if idx < 0 or idx >= len(operators):
                    send_text_message(from_phone, "Operatore non valido.", phone_number_id)
                    return
                sess["picked_operator"] = idx

            sess["state"] = "PERIOD"
            save_session(key, sess)
            _send_period_buttons(from_phone, phone_number_id)
            return

    # ── PERIOD ───────────────────────────────────────────────
    if state in ("DAY", "PERIOD") and not (interactive_id and interactive_id.startswith("day_")):
        if interactive_id and interactive_id.startswith("period_"):
            parts = interactive_id.split("_")
            try:
                start_off = int(parts[1])
                end_off = int(parts[2])
            except (ValueError, IndexError):
                return

            dur = int(sess.get("picked_total_min") or 30)
            base_day = dt.datetime.now(tz).date() + dt.timedelta(days=start_off)
            picked_op = sess.get("picked_operator")
            op_list = operators if picked_op is None else [operators[picked_op]]

            days = list_available_days(hours, op_list, base_day, dur, slot_minutes, tz, min(10, end_off - start_off))
            if not days:
                send_text_message(from_phone, "Non trovo disponibilità in questo periodo. Prova un altro intervallo.", phone_number_id)
                return

            _send_days_menu(from_phone, days, phone_number_id)
            sess["state"] = "DAY_SELECT"
            save_session(key, sess)
            return
        elif interactive_id == "ACT_CHANGE":
            sess["picked"] = []
            sess["picked_names"] = []
            sess["picked_total_min"] = 0
            sess.pop("day", None)
            sess.pop("pending_start", None)
            sess.pop("pending_operator", None)
            sess.pop("reschedule_target", None)
            sess["state"] = "SERVICES"
            save_session(key, sess)
            send_text_message(from_phone, "Ok, scegli di nuovo il servizio:", phone_number_id)
            _send_services_menu(from_phone, shop, services, phone_number_id)
            return

        _send_period_buttons(from_phone, phone_number_id)
        sess["state"] = "PERIOD"
        save_session(key, sess)
        return

    # ── DAY_SELECT ───────────────────────────────────────────
    if state == "DAY_SELECT":
        # Permetti cambio periodo anche da DAY_SELECT
        if interactive_id and interactive_id.startswith("period_"):
            parts = interactive_id.split("_")
            try:
                start_off = int(parts[1])
                end_off = int(parts[2])
            except (ValueError, IndexError):
                return
            dur = int(sess.get("picked_total_min") or 30)
            base_day = dt.datetime.now(tz).date() + dt.timedelta(days=start_off)
            picked_op = sess.get("picked_operator")
            op_list = operators if picked_op is None else [operators[picked_op]]
            days = list_available_days(hours, op_list, base_day, dur, slot_minutes, tz, min(10, end_off - start_off))
            if not days:
                send_text_message(from_phone, "Non trovo disponibilità.", phone_number_id)
                return
            _send_days_menu(from_phone, days, phone_number_id)
            save_session(key, sess)
            return

        if interactive_id and interactive_id.startswith("day_"):
            day_str = interactive_id.replace("day_", "")
            sess["day"] = day_str
            sess["state"] = "TIME_RANGE"
            save_session(key, sess)

            try:
                day = dt.date.fromisoformat(day_str)
            except ValueError:
                send_text_message(from_phone, "Data non valida.", phone_number_id)
                return

            ranges = hours.get(day.weekday(), []) or []
            dur = int(sess.get("picked_total_min") or 30)
            picked_op = sess.get("picked_operator")
            op_list = operators if picked_op is None else [operators[picked_op]]
            all_slots = list_free_slots_for_day(hours, op_list, day, dur, slot_minutes, tz, MAX_TIME_OPTIONS)

            if not ranges:
                time_buttons = [
                    {"id": "fascia_mattina", "title": "(09:00–12:00)"},
                    {"id": "fascia_pomeriggio", "title": "(12:00–16:00)"},
                    {"id": "fascia_sera", "title": "(16:00–20:00)"},
                ]
            else:
                open_min = min(st.hour * 60 + st.minute for st, _ in ranges)
                close_min = max(en.hour * 60 + en.minute for _, en in ranges)
                total = close_min - open_min

                def fmt(hm):
                    return f"{hm // 60:02d}:{hm % 60:02d}"

                fasce_meta = []
                if total < 180:
                    fasce_meta.append(("fascia_unica", f"Tutto ({fmt(open_min)}–{fmt(close_min)})", open_min, close_min))
                else:
                    step = total // 3
                    h1, h2, h3, h4 = open_min, open_min + step, open_min + 2 * step, close_min
                    fasce_meta.append(("fascia_mattina", f"({fmt(h1)}–{fmt(h2)})", h1, h2))
                    fasce_meta.append(("fascia_pomeriggio", f"({fmt(h2)}–{fmt(h3)})", h2, h3))
                    fasce_meta.append(("fascia_sera", f"({fmt(h3)}–{fmt(h4)})", h3, h4))

                time_buttons = []
                for fid, label, f_start, f_end in fasce_meta:
                    for st_slot, _op in all_slots:
                        st_min = st_slot.hour * 60 + st_slot.minute
                        if f_start <= st_min <= f_end - dur:
                            time_buttons.append({"id": fid, "title": label[:20]})
                            break

                if not time_buttons:
                    send_text_message(from_phone, "Nessuna fascia disponibile. Scegli un altro giorno.", phone_number_id)
                    send_interactive_buttons(
                        from_phone, "Cosa vuoi fare?",
                        [{"id": "ACT_BACK_DAY", "title": "Cambia giorno"}], phone_number_id,
                    )
                    return

            send_interactive_buttons(from_phone, "In che fascia oraria preferisci?", time_buttons[:3], phone_number_id)
            return

    # ── TIME_RANGE + TIME_SELECT (fascia → slot) ─────────────
    if state in ("TIME_RANGE", "TIME_SELECT"):
        if interactive_id and interactive_id.startswith("fascia_"):
            try:
                day = dt.date.fromisoformat(sess.get("day", ""))
            except ValueError:
                send_text_message(from_phone, "Errore sessione. Scrivi qualcosa per ricominciare.", phone_number_id)
                clear_session(key)
                return

            dur = sess.get("picked_total_min", 30)
            ranges = hours.get(day.weekday(), []) or []

            open_min = 9 * 60
            close_min = 20 * 60
            if ranges:
                open_min = min(st.hour * 60 + st.minute for st, _ in ranges)
                close_min = max(en.hour * 60 + en.minute for _, en in ranges)
            total = close_min - open_min

            if total < 180 or interactive_id == "fascia_unica":
                start_min, end_min = open_min, close_min
            else:
                step = total // 3
                h1, h2, h3, h4 = open_min, open_min + step, open_min + 2 * step, close_min
                if interactive_id == "fascia_mattina":
                    start_min, end_min = h1, h2
                elif interactive_id == "fascia_pomeriggio":
                    start_min, end_min = h2, h3
                elif interactive_id == "fascia_sera":
                    start_min, end_min = h3, h4
                else:
                    start_min, end_min = open_min, close_min

            picked_op = sess.get("picked_operator")
            op_list = operators if picked_op is None else [operators[picked_op]]
            slots = []
            for st_slot, op in list_free_slots_for_day(hours, op_list, day, dur, slot_minutes, tz, MAX_TIME_OPTIONS):
                st_min = st_slot.hour * 60 + st_slot.minute
                if start_min <= st_min <= end_min - dur:
                    slots.append((st_slot, op))

            if not slots:
                send_text_message(from_phone, "Nessun orario libero in questa fascia.", phone_number_id)
                send_interactive_buttons(
                    from_phone, "Cosa vuoi fare?",
                    [{"id": "ACT_BACK_DAY", "title": "Cambia giorno"}], phone_number_id,
                )
                return

            auto_assign = bool(sess.get("auto_assign_operator"))
            is_reschedule = bool(sess.get("reschedule_target"))
            show_operator = not (auto_assign or is_reschedule)
            _send_times_menu(from_phone, slots, phone_number_id, show_operator=show_operator)
            sess["state"] = "TIME_SELECT"
            save_session(key, sess)
            return

        if interactive_id and interactive_id.startswith("slot_"):
            start = dt.datetime.fromisoformat(interactive_id.replace("slot_", ""))
            dur = sess.get("picked_total_min", 30)
            end = start + dt.timedelta(minutes=dur)

            picked_op = sess.get("picked_operator")
            op_list = operators if picked_op is None else [operators[picked_op]]
            op = find_free_operator_for_slot(op_list, start, end, tz)
            if not op:
                send_text_message(from_phone, "⚠️ L'orario non è più disponibile. Scegli un altro.", phone_number_id)
                return

            sess["pending_start"] = start.isoformat()
            sess["pending_operator"] = op
            sess["state"] = "CONFIRM"
            save_session(key, sess)

            services_txt = " + ".join(sess.get("picked_names") or []) or "Servizio"
            send_interactive_buttons(
                from_phone,
                "Riepilogo appuntamento:\n"
                f"• Servizio: *{services_txt}*\n"
                f"• Quando: *{start.astimezone(tz).strftime('%d/%m/%Y %H:%M')}*\n\n"
                "Confermi?",
                [{"id": "ACT_BOOK", "title": "Prenota"}, {"id": "ACT_CHANGE", "title": "Cambia"}],
                phone_number_id,
            )
            return

        if interactive_id == "ACT_BACK_DAY":
            sess["state"] = "PERIOD"
            save_session(key, sess)
            _send_period_buttons(from_phone, phone_number_id)
            return

    # ── CONFIRM ──────────────────────────────────────────────
    if state == "CONFIRM":
        if interactive_id == "ACT_BOOK":
            start = dt.datetime.fromisoformat(sess["pending_start"])
            op = sess["pending_operator"]
            end = start + dt.timedelta(minutes=sess.get("picked_total_min", 30))

            if sess.get("reschedule_target"):
                delete_event(
                    sess["reschedule_target"]["calendar_id"],
                    sess["reschedule_target"]["event_id"],
                )

            event_id = create_booking_event(
                op["calendar_id"],
                start, end,
                " + ".join(sess["picked_names"]),
                sess.get("customer_name", "Cliente"),
                from_phone,
                shop.get("name", ""),
                op.get("operator_name", ""),
                uuid.uuid4().hex[:8],
                booking_key(shop_id, from_phone, " + ".join(sess["picked_names"]), start),
            )

            try:
                from services.customer_service import update_customer_after_booking
                update_customer_after_booking(
                    from_phone, shop_id,
                    " + ".join(sess["picked_names"]),
                    start,
                    customer_name=sess.get("customer_name"),
                    last_seen_phone_number_id=phone_number_id,
                )
            except Exception as e:
                log.warning("update_customer_after_booking failed: %s", e)

            services_txt = " + ".join(sess.get("picked_names") or []) or "Servizio"
            when_txt = start.astimezone(tz).strftime("%d/%m/%Y %H:%M")

            if sess.get("reschedule_target"):
                msg_cliente = "🔁 Appuntamento spostato correttamente!"
                msg_owner = f"🔁 Spostato\nCliente: {sess.get('customer_name', from_phone)}\nQuando: {when_txt}\nServizio: {services_txt}"
            else:
                msg_cliente = "Appuntamento confermato!"
                msg_owner = f"Nuovo appuntamento\nCliente: {sess.get('customer_name', from_phone)}\nQuando: {when_txt}\nServizio: {services_txt}"

            notify_owner(shop, msg_owner, phone_number_id)
            clear_session(key)

            send_text_message(
                from_phone,
                f"{msg_cliente}\n"
                f"• Servizio: *{services_txt}*\n"
                f"• Quando: *{when_txt}*\n\n"
                "Puoi disdire o spostare *fino a 24 ore prima*.",
                phone_number_id,
            )
            send_interactive_buttons(
                from_phone, "Gestisci l'appuntamento:",
                [{"id": "ACT_RESCHEDULE", "title": "Sposta"}, {"id": "ACT_CANCEL", "title": "Disdici"}],
                phone_number_id,
            )
            return

        if interactive_id == "ACT_CHANGE":
            sess["picked"] = []
            sess["picked_names"] = []
            sess["picked_total_min"] = 0
            sess.pop("day", None)
            sess.pop("pending_start", None)
            sess.pop("pending_operator", None)
            sess.pop("reschedule_target", None)
            sess["state"] = "SERVICES"
            save_session(key, sess)
            send_text_message(from_phone, "Ok, scegli di nuovo:", phone_number_id)
            _send_services_menu(from_phone, shop, services, phone_number_id)
            return

    # ── Fallback: torna a WELCOME ────────────────────────────
    clear_session(key)
    sess = {"state": "WELCOME"}
    save_session(key, sess)
    handle_bot(shop, from_phone, contact_name, "", None, phone_number_id)
