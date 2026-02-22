"""
Blueprint – Webhook WhatsApp (Meta Cloud API).
Gestisce la verifica del webhook e i messaggi in arrivo.
"""

import logging
from flask import Blueprint, request, jsonify, current_app
from services.whatsapp_service import send_text_message
from services.customer_service import upsert_customer_shop, get_customer
from services.sheets_service import get_shop_by_id
from services.bot_flow import handle_bot
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


# ── POST /webhook  – Ricezione messaggi ───────────────────────
@webhook_bp.route("/webhook", methods=["POST"])
def webhook_receive():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "no data"}), 400

    try:
        entries = data.get("entry", [])
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                messages = value.get("messages", [])
                contacts = value.get("contacts", [])
                metadata = value.get("metadata", {})
                phone_number_id = metadata.get("phone_number_id", "")

                for i, msg in enumerate(messages):
                    from_phone = msg.get("from", "")
                    msg_type = msg.get("type", "")

                    # Nome del contatto
                    contact_name = ""
                    if i < len(contacts):
                        profile = contacts[i].get("profile", {})
                        contact_name = profile.get("name", "")

                    # ── Testo del messaggio ───────────────────
                    incoming_text = ""
                    interactive_reply = None

                    if msg_type == "text":
                        incoming_text = msg["text"].get("body", "").strip()
                    elif msg_type == "interactive":
                        interactive = msg.get("interactive", {})
                        ir_type = interactive.get("type", "")
                        if ir_type == "button_reply":
                            interactive_reply = interactive.get("button_reply", {})
                        elif ir_type == "list_reply":
                            interactive_reply = interactive.get("list_reply", {})

                    # ── Intercetta START_<shop_id> ────────────
                    if incoming_text.startswith("START_"):
                        shop_id = incoming_text.replace("START_", "").strip()
                        shop = get_shop_by_id(shop_id)
                        if shop:
                            upsert_customer_shop(
                                from_phone,
                                shop_id,
                                customer_name=contact_name,
                                last_seen_phone_number_id=phone_number_id,
                                touch_updated_at=True,
                            )
                            handle_bot(shop, from_phone, contact_name, "", None, phone_number_id)
                            log.info("START_%s → avviato bot per %s", shop_id, from_phone)
                            continue

                    # ── Messaggio normale → risolvi shop dal cliente ──
                    customer = get_customer(from_phone)
                    if customer:
                        shop = get_shop_by_id(customer["shop_id"])
                        if shop:
                            handle_bot(shop, from_phone, contact_name,
                                       incoming_text, interactive_reply, phone_number_id)
                            log.info("Messaggio da %s (shop %s): %s",
                                     from_phone, customer["shop_id"],
                                     incoming_text[:80] if incoming_text else "[interactive]")
                            continue

                    log.warning("Messaggio da %s ma nessuno shop associato", from_phone)

    except Exception:
        log.exception("Errore nel webhook")

    # Rispondi sempre 200 a Meta per evitare retry
    return jsonify({"status": "ok"}), 200
