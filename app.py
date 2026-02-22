"""
RispondiTu – Chatbot Multi-Tenant per Parrucchieri
Applicazione Flask principale.
"""

import os
from flask import Flask
from config.settings import Config
from blueprints.landing import landing_bp
from blueprints.webhook import webhook_bp
from blueprints.cron import cron_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # ── Registrazione blueprints ──────────────────────────────
    app.register_blueprint(landing_bp)
    app.register_blueprint(webhook_bp)
    app.register_blueprint(cron_bp)

    # ── Homepage di servizio ──────────────────────────────────
    @app.route("/")
    def index():
        from services.sheets_service import get_all_shops
        shops = get_all_shops()
        links = "".join(
            f'<li style="margin:8px 0"><a href="/s/{sid}" style="color:#e94560;font-size:18px">{s.get("name",sid)}</a>'
            f' — <code>/s/{sid}</code></li>'
            for sid, s in shops.items()
        )
        return (
            '<div style="font-family:Inter,Arial,sans-serif;max-width:600px;margin:60px auto;padding:24px">'
            '<h2>✅ RispondiTu è online</h2>'
            '<p style="color:#666">Clicca su uno shop per vedere la landing page:</p>'
            f'<ul style="list-style:none;padding:0">{links}</ul>'
            '<hr style="margin:24px 0;border-color:#eee">'
            '<p style="font-size:13px;color:#999">'
            'Endpoints: <code>/s/&lt;shop_id&gt;</code> · <code>/webhook</code> · <code>/health</code></p>'
            '</div>'
        )

    # ── 404 handler ───────────────────────────────────────────
    @app.errorhandler(404)
    def page_not_found(e):
        return (
            "<h2>404 – Pagina non trovata</h2>"
            "<p>La pagina che cerchi non esiste.</p>"
            '<p><a href="/">Torna alla home</a></p>'
        ), 404

    return app


# ── Entry-point per Gunicorn / sviluppo locale ───────────────
app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
