"""
Blueprint – Webhook WhatsApp (Meta Cloud API).
Gestisce la verifica del webhook e i messaggi in arrivo.
Processa i messaggi in background per rispondere subito 200 a Meta.

Supporta:
- Numeri dedicati (un phone_number_id → uno shop)
- Numeri condivisi (SHOP=... dal QR/link)
- Mapping persistente cliente → shop (foglio customers)
- Dedup messaggi (seen_message)
"""
from __future__ import annotations

import logging
import threading
from flask import Blueprint, request, jsonify, current_app
from utils.meta_signature import verify_signature

log = logging.getLogger(__name__)
webhook_bp = Blueprint("webhook", __name__)


# ── GET  /webhook  – Verifica del webhook (Meta) ─────────────
@webhook_bp.route("/webhook", methods=["GET"])
def webhook_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == current_app.config["META_VERIFY_TOKEN"]:
        log.info("Webhook verificato con successo.")
        return challenge, 200

    log.warning("Verifica webhook fallita – token non valido.")
    return "Forbidden", 403


def _is_meta_sample_payload(display_phone: str, phone_number_id: str) -> bool:
    """Rileva i payload di test di Meta (numeri finti)."""
    from utils.helpers import norm_phone, norm_text
    return norm_phone(display_phone) == "16505551111" or norm_text(phone_number_id) == "123456123"


def _parse_incoming(m: dict) -> tuple[str, str | None]:
    """
    Estrae (testo, interactive_id) da un messaggio.
    - Testo: interactive_id=None
    - Bottone/lista: testo=title, interactive_id=id
    """
    mtype = m.get("type", "")
    if mtype == "text":
        return ((m.get("text") or {}).get("body") or "").strip(), None

    if mtype == "interactive":
        inter = m.get("interactive") or {}
        itype = inter.get("type")
        if itype == "button_reply":
            rep = inter.get("button_reply") or {}
            return rep.get("title", ""), rep.get("id")
        if itype == "list_reply":
            rep = inter.get("list_reply") or {}
            return rep.get("title", ""), rep.get("id")

    return "", None


def _process_message(app, data):
    """Processa i messaggi in un thread separato (con app context)."""
    with app.app_context():
        from services.whatsapp_service import send_text_message
        from services.customer_service import upsert_customer_shop, get_customer_shop_id
        from services.sheets_service import get_shop_by_id, get_shop_by_phone_number_id
        from services.session_service import seen_message
        from services.bot_flow import handle_bot
        from utils.helpers import norm_phone, norm_text, extract_shop_hint, strip_shop_hint

        try:
            for entry in (data.get("entry") or []):
                for ch in (entry.get("changes") or []):
                    value = ch.get("value") or {}
                    metadata = value.get("metadata") or {}
                    display_phone_number = metadata.get("display_phone_number", "")
                    phone_number_id = (metadata.get("phone_number_id") or "").strip()

                    # Skip payload di test Meta
                    if _is_meta_sample_payload(display_phone_number, phone_number_id):
                        continue

                    contacts = value.get("contacts") or []
                    contact_name = None
                    if contacts:
                        profile = (contacts[0].get("profile") or {})
                        contact_name = profile.get("name")

                    # Cerca shop per numero dedicato
                    forced_shop, is_forced = get_shop_by_phone_number_id(phone_number_id)

                    for m in (value.get("messages") or []):
                        msg_id = m.get("id", "")
                        if msg_id and seen_message(msg_id):
                            continue

                        from_phone = m.get("from", "")
                        if not from_phone:
                            continue

                        incoming_text, interactive_id = _parse_incoming(m)
                        incoming_text = incoming_text or ""

                        hint = extract_shop_hint(incoming_text)

                        log.info(
                            "[WEBHOOK] from=%s type=%s interactive=%s forced=%s shop=%s",
                            from_phone, m.get("type"), interactive_id, is_forced,
                            (forced_shop.get("id") if forced_shop else ""),
                        )

                        # ── 1) NUMERO DEDICATO (forzato) ─────────
                        if forced_shop and is_forced and forced_shop.get("id"):
                            if hint and norm_text(hint) != norm_text(forced_shop["id"]):
                                incoming_text = strip_shop_hint(incoming_text)
                                hint = None

                            try:
                                upsert_customer_shop(
                                    from_phone, forced_shop["id"],
                                    customer_name=contact_name,
                                    last_seen_phone_number_id=phone_number_id,
                                )
                            except Exception as e:
                                log.warning("upsert forced failed: %s", e)

                            handle_bot(forced_shop, from_phone, contact_name, incoming_text, interactive_id, phone_number_id)
                            continue

                        # ── 2) SHOP=... dal QR/link ──────────────
                        if hint:
                            hinted_shop = get_shop_by_id(hint)
                            if hinted_shop:
                                try:
                                    upsert_customer_shop(
                                        from_phone, hint,
                                        customer_name=contact_name,
                                        last_seen_phone_number_id=phone_number_id,
                                    )
                                except Exception as e:
                                    log.warning("upsert hint failed: %s", e)

                                incoming_text = strip_shop_hint(incoming_text)

                                if not incoming_text and not interactive_id:
                                    send_text_message(
                                        from_phone,
                                        f"✅ Connesso a *{hinted_shop.get('name', 'questa sede')}*.",
                                        phone_number_id,
                                    )

                                handle_bot(hinted_shop, from_phone, contact_name, "", None, phone_number_id)
                                continue

                        # ── 3) Mapping persistente (customers) ───
                        saved_shop_id = None
                        try:
                            saved_shop_id = get_customer_shop_id(from_phone)
                        except Exception as e:
                            log.warning("get_customer_shop_id failed: %s", e)

                        shop = get_shop_by_id(saved_shop_id) if saved_shop_id else None
                        if not shop:
                            send_text_message(
                                from_phone,
                                "Per iniziare apri il bot dal QR/link del negozio (contiene `SHOP=...`).",
                                phone_number_id,
                            )
                            continue

                        # Touch mapping
                        try:
                            upsert_customer_shop(
                                from_phone, shop["id"],
                                customer_name=contact_name,
                                last_seen_phone_number_id=phone_number_id,
                            )
                        except Exception as e:
                            log.warning("touch failed: %s", e)

                        handle_bot(shop, from_phone, contact_name, incoming_text, interactive_id, phone_number_id)

        except Exception:
            log.exception("Errore nel processing del messaggio")


# ── POST /webhook  – Ricezione messaggi ───────────────────────
@webhook_bp.route("/webhook", methods=["POST"])
def webhook_receive():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "no data"}), 400

    # Rispondi subito 200 a Meta, processa in background
    app = current_app._get_current_object()
    thread = threading.Thread(target=_process_message, args=(app, data), daemon=True)
    thread.start()

    return jsonify({"status": "ok"}), 200
