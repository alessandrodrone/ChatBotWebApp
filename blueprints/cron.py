"""
Blueprint – Cron / Health-check / Promemoria 24h.
Endpoint /cron/reminders da chiamare via scheduler esterno (ogni 30-60 min).
"""
from __future__ import annotations

import logging
import datetime as dt

from flask import Blueprint, jsonify, request, current_app

log = logging.getLogger(__name__)
cron_bp = Blueprint("cron", __name__)


@cron_bp.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200


@cron_bp.route("/cron/reminders", methods=["POST", "GET"])
def cron_reminders():
    """
    Invia promemoria WhatsApp 24h prima degli appuntamenti.
    Protetto da CRON_TOKEN (header X-Cron-Token o query ?token=...).
    """
    cron_token = current_app.config.get("CRON_TOKEN", "")
    if cron_token:
        token = request.headers.get("X-Cron-Token", "") or request.args.get("token", "")
        if token != cron_token:
            return "Forbidden", 403

    stats = _run_24h_reminders()
    return jsonify({"ok": True, **stats}), 200


def _run_24h_reminders() -> dict:
    """Cerca eventi tra 24h ± finestra e invia promemoria WhatsApp."""
    from services.sheets_service import get_all_shops, get_operators_for_shop
    from services.whatsapp_service import send_text_message
    from services.calendar_service import patch_event_private_props, _get_calendar_client, _event_dt
    from utils.helpers import norm_phone, norm_text, shop_tz

    stats = {"checked": 0, "sent": 0, "skipped": 0, "errors": 0}

    cal = _get_calendar_client()
    if cal is None:
        log.warning("Calendar client non disponibile per reminders")
        return stats

    shops = get_all_shops()
    window_min = current_app.config.get("REMINDER_WINDOW_MINUTES", 60)

    for shop_id, shop in shops.items():
        tz = shop_tz(shop)
        phone_number_id = norm_text(shop.get("phone_number_id")) or current_app.config.get("META_PHONE_NUMBER_ID", "")
        operators = get_operators_for_shop(shop_id)
        if not operators:
            continue

        now_local = dt.datetime.now(tz)
        half = max(1, window_min // 2)
        start_win = (now_local + dt.timedelta(hours=24) - dt.timedelta(minutes=half)).replace(microsecond=0)
        end_win = (now_local + dt.timedelta(hours=24) + dt.timedelta(minutes=half)).replace(microsecond=0)

        for op in operators:
            cal_id = op.get("calendar_id")
            if not cal_id:
                continue
            try:
                evs = cal.events().list(
                    calendarId=cal_id,
                    timeMin=start_win.isoformat(),
                    timeMax=end_win.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=250,
                ).execute().get("items", [])
            except Exception as e:
                log.warning("reminders list failed %s: %s", cal_id, e)
                stats["errors"] += 1
                continue

            for ev in evs:
                stats["checked"] += 1
                ep = ((ev.get("extendedProperties") or {}).get("private") or {})
                customer_phone = norm_phone(ep.get("customer_phone", ""))
                if not customer_phone:
                    stats["skipped"] += 1
                    continue
                if str(ep.get("reminder_24h_sent", "0")).strip() == "1":
                    stats["skipped"] += 1
                    continue

                start_dt = _event_dt(ev, tz)
                if not start_dt:
                    stats["skipped"] += 1
                    continue

                service = norm_text(ep.get("service") or ev.get("summary") or "")
                shop_name = norm_text(ep.get("shop") or shop.get("name") or "")
                operator_name = norm_text(ep.get("operator") or op.get("operator_name") or "")
                owner_contact = norm_phone(shop.get("owner_phone", "") or "") or shop_name

                text = (
                    f"⏰ Promemoria appuntamento\n"
                    f"Domani alle *{start_dt.strftime('%H:%M')}*\n"
                    f"{shop_name}\n"
                    f"{service}\n"
                    f"{operator_name}\n\n"
                    f"⚠️ Mancano meno di 24 ore. Per modifiche, contatta: {owner_contact}"
                )

                try:
                    send_text_message(customer_phone, text, phone_number_id)
                    from utils.helpers import utc_now_iso
                    patch_event_private_props(
                        cal_id, ev.get("id", ""),
                        {"reminder_24h_sent": "1", "reminder_24h_ts": utc_now_iso()},
                    )
                    stats["sent"] += 1
                except Exception as e:
                    log.warning("reminder send failed %s: %s", customer_phone, e)
                    stats["errors"] += 1

    log.info("Reminders completati: %s", stats)
    return stats
