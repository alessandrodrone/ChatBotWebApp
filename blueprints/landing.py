"""
Blueprint – Landing page dinamica per ogni shop.
URL: /s/<shop_id>
"""

from flask import Blueprint, render_template, current_app
from services.sheets_service import get_shop_by_id

landing_bp = Blueprint("landing", __name__)


@landing_bp.route("/s/<shop_id>")
def landing_shop(shop_id):
    """
    Mostra la landing page personalizzata per lo shop.
    Il cliente clicca "Prenota su WhatsApp" → si apre WhatsApp
    con messaggio precompilato START_<shop_id>.
    """
    shop = get_shop_by_id(shop_id)
    if not shop:
        return render_template("404_shop.html", shop_id=shop_id), 404

    wa_number = shop.get("phone") or current_app.config["WHATSAPP_NUMBER"]
    wa_link = f"https://wa.me/{wa_number}?text=START_{shop_id}"

    return render_template(
        "landing_shop.html",
        shop=shop,
        wa_link=wa_link,
    )
