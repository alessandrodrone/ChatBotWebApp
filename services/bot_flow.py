"""
Bot Flow – logica conversazionale completa.

Flusso: WELCOME → SERVICES → OPERATOR → PERIOD → DAY_SELECT →
        TIME_RANGE → TIME_SELECT → CONFIRM → [NOTES] → booking
+ Gestione disdetta/spostamento con policy 24h
+ Blocco clienti indesiderati
+ Note prenotazione (booking_notes_prompt)
+ Anticipo minimo (min_advance_hours)
+ Limite slot globale (max_slots_per_half_hour)
+ Operatore nascondibile nei promemoria
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
    is_customer_blocked,
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
    parse_work_phases,
)
from utils.helpers import (
    norm_phone, norm_text, safe_lower, parse_int, parse_bool,
    shop_tz, WEEKDAYS_IT,
)

log = logging.getLogger(__name__)

MAX_TIME_OPTIONS = 48


# ══════════════════════════════════════════════════════════════
# MENU HELPERS – tutte ricevono shop per credenziali WA
# ══════════════════════════════════════════════════════════════

def _send_services_menu(shop, from_phone, services, picked_ids=None):
    picked_ids = picked_ids or []
    rows = []
    for i, s in enumerate(services, 1):
        sid = f"svc_{i}"
        if sid in picked_ids:
            continue
        rows.append((sid, s.get("name", f"Servizio {i}"), ""))
    if not rows:
        send_text_message(shop, from_phone, "Hai già selezionato tutti i servizi disponibili.")
        return
    send_list_message(shop, from_phone, "Scegli un servizio:", "Servizi", rows)


def _send_days_menu(shop, from_phone, days):
    rows = []
    for d in days:
        rid = f"day_{d.isoformat()}"
        wd = WEEKDAYS_IT[d.weekday()]
        title = f"{wd} {d.strftime('%d/%m')}"
        rows.append((rid, title, ""))
    send_list_message(shop, from_phone, "Scegli il giorno:", "Giorni", rows)


def _send_times_menu(shop, from_phone, slots, show_operator=True):
    max_opts = current_app.config.get("MAX_TIME_OPTIONS", MAX_TIME_OPTIONS)
    if not slots:
        send_text_message(shop, from_phone, "Nessun orario libero per questo giorno.")
        send_interactive_buttons(
            shop, from_phone, "Cosa vuoi fare?",
            [{"id": "ACT_BACK_DAY", "title": "Cambia giorno"}],
        )
        return
    rows = []
    for st, op in slots[:max_opts]:
        rid = f"slot_{st.isoformat()}"
        title = st.strftime("%H:%M")
        desc = f"con {op.get('operator_name', 'Operatore')}" if show_operator else ""
        rows.append((rid, title, desc))
    send_list_message(shop, from_phone, "Scegli l'orario:", "Orari", rows)
    send_interactive_buttons(
        shop, from_phone, "Se non trovi l'orario, cambia giorno:",
        [{"id": "ACT_BACK_DAY", "title": "Cambia giorno"}],
    )


def _send_period_buttons(shop, from_phone):
    buttons = [
        {"id": "period_0_10", "title": "📆 Prossimi 10 gg"},
        {"id": "period_10_20", "title": "📆 Da 10 a 20 gg"},
        {"id": "period_20_30", "title": "📆 Da 20 a 30 gg"},
    ]
    send_interactive_buttons(shop, from_phone, "Quando preferisci venire?", buttons)


def _filter_min_advance(shop, slots, tz):
    """Rimuove slot troppo vicini nel tempo (min_advance_hours)."""
    min_adv = parse_int(str(shop.get("min_advance_hours", "")), 0)
    if min_adv <= 0:
        return slots
    cutoff = dt.datetime.now(tz) + dt.timedelta(hours=min_adv)
    return [(s, o) for s, o in slots if s >= cutoff]


def _build_event_summary(shop, sess):
    """Costruisce il titolo dell'evento Calendar dal formato del negozio."""
    fmt = (shop.get("booking_title_format") or "").strip()
    if not fmt:
        fmt = "{customer_name} – {service}"
    try:
        return fmt.format(
            customer_name=sess.get("customer_name", "Cliente"),
            service=" + ".join(sess.get("picked_names") or []),
            note=sess.get("booking_notes", ""),
            phone=norm_phone(sess.get("_from_phone", "")),
        ).strip()
    except (KeyError, ValueError):
        return f"{sess.get('customer_name', 'Cliente')} – {' + '.join(sess.get('picked_names') or [])}"


def _do_booking(shop, from_phone, sess, operators, tz, phone_number_id, key):
    """Esegue la prenotazione effettiva (creazione evento Calendar)."""
    shop_id = shop.get("id", "")
    start = dt.datetime.fromisoformat(sess["pending_start"])
    op = sess["pending_operator"]
    end = start + dt.timedelta(minutes=sess.get("picked_total_min", 30))

    if sess.get("reschedule_target"):
        delete_event(
            sess["reschedule_target"]["calendar_id"],
            sess["reschedule_target"]["event_id"],
        )

    services_txt = " + ".join(sess.get("picked_names") or []) or "Servizio"
    summary = _build_event_summary(shop, sess)

    event_id = create_booking_event(
        op["calendar_id"],
        start, end,
        services_txt,
        sess.get("customer_name", "Cliente"),
        from_phone,
        shop.get("name", ""),
        op.get("operator_name", ""),
        uuid.uuid4().hex[:8],
        booking_key(shop_id, from_phone, services_txt, start),
        summary_override=summary,
        booking_notes=sess.get("booking_notes", ""),
        work_phases_json=sess.get("picked_work_phases", ""),
    )

    try:
        from services.customer_service import update_customer_after_booking
        update_customer_after_booking(
            from_phone, shop_id, services_txt, start,
            customer_name=sess.get("customer_name"),
            last_seen_phone_number_id=phone_number_id,
        )
    except Exception as e:
        log.warning("update_customer_after_booking failed: %s", e)

    when_txt = start.astimezone(tz).strftime("%d/%m/%Y %H:%M")

    if sess.get("reschedule_target"):
        msg_cliente = "🔁 Appuntamento spostato correttamente!"
        msg_owner = (
            f"🔁 Spostato\nCliente: {sess.get('customer_name', from_phone)}"
            f"\nQuando: {when_txt}\nServizio: {services_txt}"
        )
    else:
        msg_cliente = "Appuntamento confermato!"
        msg_owner = (
            f"Nuovo appuntamento\nCliente: {sess.get('customer_name', from_phone)}"
            f"\nQuando: {when_txt}\nServizio: {services_txt}"
        )

    if sess.get("booking_notes"):
        msg_owner += f"\nNote: {sess['booking_notes']}"

    notify_owner(shop, msg_owner)
    clear_session(key)

    send_text_message(
        shop, from_phone,
        f"{msg_cliente}\n"
        f"• Servizio: *{services_txt}*\n"
        f"• Quando: *{when_txt}*\n\n"
        "Puoi disdire o spostare *fino a 24 ore prima*.",
    )
    send_interactive_buttons(
        shop, from_phone, "Gestisci l'appuntamento:",
        [{"id": "ACT_RESCHEDULE", "title": "Sposta"}, {"id": "ACT_CANCEL", "title": "Disdici"}],
    )


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

    # ── CLIENTE BLOCCATO ─────────────────────────────────────
    blocked, blocked_msg = is_customer_blocked(shop_id, from_phone)
    if blocked:
        msg = blocked_msg or (shop.get("blocked_message") or "").strip()
        if not msg:
            msg = "Mi dispiace, al momento non è possibile effettuare prenotazioni."
        send_text_message(shop, from_phone, msg)
        return

    services = get_services_for_shop(shop_id)
    hours = load_hours_parsed(shop_id)
    operators = get_operators_for_shop(shop_id)
    slot_minutes = parse_int(
        str(shop.get("slot_minutes", "")),
        current_app.config.get("DEFAULT_SLOT_MINUTES", 30),
    )
    max_concurrent = parse_int(str(shop.get("max_slots_per_half_hour", "")), 0)

    sess = get_session(key)
    if not sess:
        sess = {
            "state": "WELCOME",
            "picked": [],
            "picked_names": [],
            "picked_total_min": 0,
            "picked_work_phases": "",
            "auto_assign_operator": False,
        }
    if contact_name:
        sess["customer_name"] = contact_name
    sess["_from_phone"] = from_phone
    low = safe_lower(incoming_text)

    # ══════════════════════════════════════════════════════════
    # CANCEL / RESCHEDULE
    # ══════════════════════════════════════════════════════════
    if interactive_id in {"ACT_CANCEL", "ACT_RESCHEDULE"} or "disd" in low or "sposta" in low:
        found = find_upcoming_customer_event(operators, from_phone, tz)
        if not found:
            send_text_message(shop, from_phone, "Non vedo appuntamenti futuri.")
            return
        cal_id, ev_id, ev = found
        if not can_change_booking(ev, tz):
            owner_phone = norm_phone(shop.get("owner_phone", "") or "") or shop.get("name", "il negozio")
            send_text_message(
                shop, from_phone,
                f"⚠️ Mancano meno di 24 ore al tuo appuntamento.\n"
                f"Per modifiche, contatta il negozio: {owner_phone}",
            )
            notify_owner(
                shop,
                f"⚠️ Cliente {contact_name or from_phone} ha tentato modifica <24h.",
            )
            return

        if interactive_id == "ACT_CANCEL" or "disd" in low:
            delete_event(cal_id, ev_id)
            clear_session(key)
            send_text_message(shop, from_phone, "❌ Appuntamento annullato correttamente.")
            notify_owner(shop, f"❌ Annullato\nCliente: {contact_name or from_phone}")
            return

        if interactive_id == "ACT_RESCHEDULE" or "sposta" in low:
            sess["reschedule_target"] = {"calendar_id": cal_id, "event_id": ev_id}
            ev_priv = ((ev.get("extendedProperties") or {}).get("private") or {})
            svc_name = norm_text(ev_priv.get("service") or ev.get("summary") or "")
            if "–" in svc_name:
                try:
                    svc_name = norm_text(svc_name.split("–", 1)[1])
                except Exception:
                    pass
            if not svc_name:
                svc_name = "Servizio"

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
            sess["picked_work_phases"] = ev_priv.get("work_phases", "")
            sess["state"] = "PERIOD"
            save_session(key, sess)
            _send_period_buttons(shop, from_phone)
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
                shop, from_phone,
                f"👋 Benvenuto da {shop.get('name', 'il nostro salone')}\n"
                "Posso aiutarti a prenotare un appuntamento in pochi secondi.\n"
                "Da dove vuoi iniziare?",
                buttons,
            )
            save_session(key, sess)
            return

        if interactive_id == "ACT_BOOK":
            sess["state"] = "SERVICES"
            save_session(key, sess)
            _send_services_menu(shop, from_phone, services, sess.get("picked"))
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
                svc = ep.get("service", "Servizio")
                show_op = str(shop.get("show_operator_in_reminder", "TRUE")).strip().upper() != "FALSE"
                op_line = ""
                if show_op and ep.get("operator"):
                    op_line = f"\n• Operatore: {ep['operator']}"

                send_text_message(
                    shop, from_phone,
                    f"📝 Hai un appuntamento:\n• Servizio: {svc}\n• Quando: {when_txt}{op_line}",
                )
                send_interactive_buttons(
                    shop, from_phone, "Cosa vuoi fare?",
                    [{"id": "ACT_RESCHEDULE", "title": "Sposta"}, {"id": "ACT_CANCEL", "title": "❌ Disdici"}],
                )
                return
            else:
                send_text_message(shop, from_phone, "Non hai appuntamenti futuri.")
                sess["state"] = "WELCOME"
                save_session(key, sess)
                return

        if interactive_id == "ACT_INFO":
            info = norm_text(shop.get("info")) or "Qui puoi inserire informazioni sul negozio."
            send_text_message(shop, from_phone, info)
            sess["state"] = "WELCOME"
            save_session(key, sess)
            return

    # ── Back to period ───────────────────────────────────────
    if interactive_id == "ACT_BACK_DAY":
        sess["state"] = "PERIOD"
        save_session(key, sess)
        _send_period_buttons(shop, from_phone)
        return

    # ── SERVICES ─────────────────────────────────────────────
    if state == "SERVICES":
        if interactive_id == "ACT_ADD":
            sess["state"] = "SERVICES"
            save_session(key, sess)
            _send_services_menu(shop, from_phone, services, sess.get("picked"))
            return

        if interactive_id == "ACT_CHANGE":
            sess["picked"] = []
            sess["picked_names"] = []
            sess["picked_total_min"] = 0
            sess["picked_work_phases"] = ""
            sess["state"] = "SERVICES"
            save_session(key, sess)
            send_text_message(shop, from_phone, "Ok, scegli di nuovo il servizio:")
            _send_services_menu(shop, from_phone, services)
            return

        if interactive_id == "ACT_NEXT":
            if not sess.get("picked_names"):
                send_text_message(shop, from_phone, "Prima scegli almeno un servizio.")
                _send_services_menu(shop, from_phone, services, sess.get("picked"))
                return

            if not operators:
                send_text_message(
                    shop, from_phone,
                    "Al momento non ci sono operatori configurati. Contatta direttamente il negozio.",
                )
                sess["state"] = "WELCOME"
                save_session(key, sess)
                return

            active_ops = [op for op in operators if op.get("active", True)]
            if active_ops and len(active_ops) > 1:
                sess["state"] = "OPERATOR"
                save_session(key, sess)
                op_buttons = [
                    {"id": f"op_{i}", "title": f"👤 {op['operator_name']}"[:20]}
                    for i, op in enumerate(operators)
                ]
                op_buttons.append({"id": "op_any", "title": "👤 Chiunque"})
                if len(op_buttons) <= 3:
                    send_interactive_buttons(shop, from_phone, "Con chi preferisci?", op_buttons)
                else:
                    rows = [(b["id"], b["title"], "") for b in op_buttons]
                    send_list_message(shop, from_phone, "Con chi preferisci?", "Operatori", rows)
                return
            elif active_ops and len(active_ops) == 1:
                sess["picked_operator"] = 0
            else:
                sess["picked_operator"] = None
                sess["auto_assign_operator"] = True

            sess["state"] = "PERIOD"
            save_session(key, sess)
            _send_period_buttons(shop, from_phone)
            return

        if interactive_id and interactive_id.startswith("svc_"):
            try:
                idx = int(interactive_id.split("_")[1])
            except (ValueError, IndexError):
                return
            if idx < 1 or idx > len(services):
                send_text_message(shop, from_phone, "Servizio non valido.")
                return

            svc = services[idx - 1]
            sess["picked"].append(interactive_id)
            sess["picked_names"].append(svc.get("name"))
            sess["picked_total_min"] += int(svc.get("duration", 30))
            # work_phases: valido solo per servizio singolo
            if len(sess["picked"]) == 1:
                sess["picked_work_phases"] = svc.get("work_phases", "")
            else:
                sess["picked_work_phases"] = ""
            save_session(key, sess)

            send_interactive_buttons(
                shop, from_phone,
                f"Hai scelto *{svc.get('name')}*\nCosa vuoi fare?",
                [
                    {"id": "ACT_ADD", "title": "Aggiungi"},
                    {"id": "ACT_CHANGE", "title": "Cambia"},
                    {"id": "ACT_NEXT", "title": "Prosegui"},
                ],
            )
            return

        _send_services_menu(shop, from_phone, services, sess.get("picked"))
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
                    send_text_message(shop, from_phone, "Operatore non valido.")
                    return
                sess["picked_operator"] = idx

            sess["state"] = "PERIOD"
            save_session(key, sess)
            _send_period_buttons(shop, from_phone)
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
            wp = parse_work_phases(sess.get("picked_work_phases", ""))

            days = list_available_days(
                hours, op_list, base_day, dur, slot_minutes, tz,
                min(10, end_off - start_off),
                max_concurrent=max_concurrent, all_operators=operators,
                work_phases=wp or None,
            )
            if not days:
                send_text_message(
                    shop, from_phone,
                    "Non trovo disponibilità in questo periodo. Prova un altro intervallo.",
                )
                return

            _send_days_menu(shop, from_phone, days)
            sess["state"] = "DAY_SELECT"
            save_session(key, sess)
            return
        elif interactive_id == "ACT_CHANGE":
            sess["picked"] = []
            sess["picked_names"] = []
            sess["picked_total_min"] = 0
            sess["picked_work_phases"] = ""
            sess.pop("day", None)
            sess.pop("pending_start", None)
            sess.pop("pending_operator", None)
            sess.pop("reschedule_target", None)
            sess["state"] = "SERVICES"
            save_session(key, sess)
            send_text_message(shop, from_phone, "Ok, scegli di nuovo il servizio:")
            _send_services_menu(shop, from_phone, services)
            return

        _send_period_buttons(shop, from_phone)
        sess["state"] = "PERIOD"
        save_session(key, sess)
        return

    # ── DAY_SELECT ───────────────────────────────────────────
    if state == "DAY_SELECT":
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
            wp = parse_work_phases(sess.get("picked_work_phases", ""))
            days = list_available_days(
                hours, op_list, base_day, dur, slot_minutes, tz,
                min(10, end_off - start_off),
                max_concurrent=max_concurrent, all_operators=operators,
                work_phases=wp or None,
            )
            if not days:
                send_text_message(shop, from_phone, "Non trovo disponibilità.")
                return
            _send_days_menu(shop, from_phone, days)
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
                send_text_message(shop, from_phone, "Data non valida.")
                return

            ranges = hours.get(day.weekday(), []) or []
            dur = int(sess.get("picked_total_min") or 30)
            picked_op = sess.get("picked_operator")
            op_list = operators if picked_op is None else [operators[picked_op]]
            wp = parse_work_phases(sess.get("picked_work_phases", ""))
            all_slots = list_free_slots_for_day(
                hours, op_list, day, dur, slot_minutes, tz, MAX_TIME_OPTIONS,
                max_concurrent=max_concurrent, all_operators=operators,
                work_phases=wp or None,
            )
            all_slots = _filter_min_advance(shop, all_slots, tz)

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
                    send_text_message(shop, from_phone, "Nessuna fascia disponibile. Scegli un altro giorno.")
                    send_interactive_buttons(
                        shop, from_phone, "Cosa vuoi fare?",
                        [{"id": "ACT_BACK_DAY", "title": "Cambia giorno"}],
                    )
                    return

            send_interactive_buttons(shop, from_phone, "In che fascia oraria preferisci?", time_buttons[:3])
            return

    # ── TIME_RANGE + TIME_SELECT ─────────────────────────────
    if state in ("TIME_RANGE", "TIME_SELECT"):
        if interactive_id and interactive_id.startswith("fascia_"):
            try:
                day = dt.date.fromisoformat(sess.get("day", ""))
            except ValueError:
                send_text_message(shop, from_phone, "Errore sessione. Scrivi qualcosa per ricominciare.")
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
            wp = parse_work_phases(sess.get("picked_work_phases", ""))
            raw_slots = list_free_slots_for_day(
                hours, op_list, day, dur, slot_minutes, tz, MAX_TIME_OPTIONS,
                max_concurrent=max_concurrent, all_operators=operators,
                work_phases=wp or None,
            )
            raw_slots = _filter_min_advance(shop, raw_slots, tz)

            slots = []
            for st_slot, op in raw_slots:
                st_min = st_slot.hour * 60 + st_slot.minute
                if start_min <= st_min <= end_min - dur:
                    slots.append((st_slot, op))

            if not slots:
                send_text_message(shop, from_phone, "Nessun orario libero in questa fascia.")
                send_interactive_buttons(
                    shop, from_phone, "Cosa vuoi fare?",
                    [{"id": "ACT_BACK_DAY", "title": "Cambia giorno"}],
                )
                return

            auto_assign = bool(sess.get("auto_assign_operator"))
            is_reschedule = bool(sess.get("reschedule_target"))
            show_operator = not (auto_assign or is_reschedule)
            _send_times_menu(shop, from_phone, slots, show_operator=show_operator)
            sess["state"] = "TIME_SELECT"
            save_session(key, sess)
            return

        if interactive_id and interactive_id.startswith("slot_"):
            start = dt.datetime.fromisoformat(interactive_id.replace("slot_", ""))
            dur = sess.get("picked_total_min", 30)
            end = start + dt.timedelta(minutes=dur)

            picked_op = sess.get("picked_operator")
            op_list = operators if picked_op is None else [operators[picked_op]]
            wp = parse_work_phases(sess.get("picked_work_phases", ""))
            op = find_free_operator_for_slot(op_list, start, end, tz, work_phases=wp or None)
            if not op:
                send_text_message(shop, from_phone, "⚠️ L'orario non è più disponibile. Scegli un altro.")
                return

            sess["pending_start"] = start.isoformat()
            sess["pending_operator"] = op
            sess["state"] = "CONFIRM"
            save_session(key, sess)

            services_txt = " + ".join(sess.get("picked_names") or []) or "Servizio"
            show_op = str(shop.get("show_operator_in_reminder", "TRUE")).strip().upper() != "FALSE"
            op_line = ""
            if show_op:
                op_line = f"\n• Operatore: *{op.get('operator_name', '')}*"

            send_interactive_buttons(
                shop, from_phone,
                "Riepilogo appuntamento:\n"
                f"• Servizio: *{services_txt}*\n"
                f"• Quando: *{start.astimezone(tz).strftime('%d/%m/%Y %H:%M')}*"
                f"{op_line}\n\n"
                "Confermi?",
                [{"id": "ACT_BOOK", "title": "Prenota"}, {"id": "ACT_CHANGE", "title": "Cambia"}],
            )
            return

        if interactive_id == "ACT_BACK_DAY":
            sess["state"] = "PERIOD"
            save_session(key, sess)
            _send_period_buttons(shop, from_phone)
            return

    # ── CONFIRM ──────────────────────────────────────────────
    if state == "CONFIRM":
        if interactive_id == "ACT_BOOK":
            notes_prompt = (shop.get("booking_notes_prompt") or "").strip()
            show_notes = str(shop.get("show_notes_to_customer", "FALSE")).strip().upper() == "TRUE"
            if notes_prompt and not sess.get("booking_notes") and show_notes:
                sess["state"] = "NOTES"
                save_session(key, sess)
                send_text_message(shop, from_phone, notes_prompt)
                return
            _do_booking(shop, from_phone, sess, operators, tz, phone_number_id, key)
            return

        if interactive_id == "ACT_CHANGE":
            sess["picked"] = []
            sess["picked_names"] = []
            sess["picked_total_min"] = 0
            sess["picked_work_phases"] = ""
            sess.pop("day", None)
            sess.pop("pending_start", None)
            sess.pop("pending_operator", None)
            sess.pop("reschedule_target", None)
            sess.pop("booking_notes", None)
            sess["state"] = "SERVICES"
            save_session(key, sess)
            send_text_message(shop, from_phone, "Ok, scegli di nuovo:")
            _send_services_menu(shop, from_phone, services)
            return

    # ── NOTES (raccolta note prima del booking) ──────────────
    if state == "NOTES":
        if incoming_text:
            sess["booking_notes"] = incoming_text
            save_session(key, sess)
            _do_booking(shop, from_phone, sess, operators, tz, phone_number_id, key)
            return
        prompt = (shop.get("booking_notes_prompt") or "Scrivi le note per l'appuntamento:").strip()
        send_text_message(shop, from_phone, prompt)
        return

    # ── Fallback: torna a WELCOME ────────────────────────────
    clear_session(key)
    sess = {"state": "WELCOME"}
    save_session(key, sess)
    handle_bot(shop, from_phone, contact_name, "", None, phone_number_id)
