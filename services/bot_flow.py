"""
Bot Flow – logica conversazionale principale.
Gestisce il flusso di prenotazione dopo che il cliente è stato
associato a uno shop.
"""
from __future__ import annotations
import logging
from services.whatsapp_service import send_text_message, send_interactive_buttons

log = logging.getLogger(__name__)


def handle_bot(
    shop: dict,
    from_phone: str,
    contact_name: str,
    incoming_text: str,
    interactive_reply: dict | None,
    phone_number_id: str,
):
    """
    Punto d'ingresso del flusso conversazionale.
    Viene chiamato dal webhook dopo aver identificato lo shop.
    """
    shop_name = shop.get("name", "il negozio")
    first_name = contact_name.split()[0] if contact_name else ""
    greeting = f" {first_name}" if first_name else ""

    # ── Messaggio di benvenuto (prima interazione / START_) ───
    if not incoming_text and interactive_reply is None:
        welcome = (
            f"Ciao{greeting}! 👋\n"
            f"Benvenuto da *{shop_name}*.\n\n"
            "Come posso aiutarti?"
        )
        buttons = [
            {"id": "btn_prenota", "title": "📅 Prenota"},
            {"id": "btn_orari", "title": "🕐 Orari"},
            {"id": "btn_info", "title": "ℹ️ Info"},
        ]
        send_interactive_buttons(from_phone, welcome, buttons, phone_number_id)
        return

    # ── Gestione risposte ai bottoni ──────────────────────────
    if interactive_reply:
        btn_id = interactive_reply.get("id", "")
        if btn_id == "btn_prenota":
            send_text_message(
                from_phone,
                f"Per prenotare da *{shop_name}*, scrivimi il giorno e l'ora che preferisci.\n"
                "Ad esempio: *Martedì alle 10:00*",
                phone_number_id,
            )
        elif btn_id == "btn_orari":
            send_text_message(
                from_phone,
                f"🕐 *Orari di {shop_name}*\n"
                "Lun-Ven: 9:00 – 19:00\n"
                "Sabato: 9:00 – 13:00\n"
                "Domenica: Chiuso",
                phone_number_id,
            )
        elif btn_id == "btn_info":
            address = shop.get("address", "")
            desc = shop.get("description", "")
            send_text_message(
                from_phone,
                f"ℹ️ *{shop_name}*\n{desc}\n📍 {address}",
                phone_number_id,
            )
        return

    # ── Messaggio di testo libero ─────────────────────────────
    send_text_message(
        from_phone,
        f"Grazie{greeting}! Ho ricevuto il tuo messaggio.\n"
        f"Un operatore di *{shop_name}* ti risponderà al più presto. 🙏",
        phone_number_id,
    )
