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

    # ── Diagnostica Google Sheets (TEMPORANEO – rimuovere dopo il test) ──
    @app.route("/debug/sheets")
    def debug_sheets():
        import json as _json
        checks = {}

        # 1. Variabile GOOGLE_SERVICE_ACCOUNT_JSON
        creds_raw = app.config.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        if not creds_raw:
            checks["GOOGLE_SERVICE_ACCOUNT_JSON"] = "❌ MANCANTE – la variabile è vuota"
        else:
            checks["GOOGLE_SERVICE_ACCOUNT_JSON"] = f"✅ presente ({len(creds_raw)} caratteri)"
            # Prova a parsare il JSON
            try:
                creds_dict = _json.loads(creds_raw)
                checks["JSON_parse"] = "✅ JSON valido"
                checks["service_account_email"] = creds_dict.get("client_email", "⚠️ campo client_email mancante")
                checks["project_id"] = creds_dict.get("project_id", "⚠️ campo project_id mancante")
            except _json.JSONDecodeError as e:
                checks["JSON_parse"] = f"❌ JSON NON VALIDO: {e}"

        # 2. Variabile GOOGLE_SHEET_ID
        sheet_id = app.config.get("GOOGLE_SHEET_ID", "")
        if not sheet_id:
            checks["GOOGLE_SHEET_ID"] = "❌ MANCANTE – la variabile è vuota"
        else:
            checks["GOOGLE_SHEET_ID"] = f"✅ {sheet_id}"

        # 3. Prova la connessione a Google Sheets
        if creds_raw and sheet_id:
            try:
                import gspread
                from google.oauth2.service_account import Credentials
                creds_dict = _json.loads(creds_raw)
                scopes = [
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ]
                credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
                gc = gspread.authorize(credentials)
                checks["gspread_auth"] = "✅ Autenticazione riuscita"

                # 4. Apri lo spreadsheet
                try:
                    spreadsheet = gc.open_by_key(sheet_id)
                    checks["spreadsheet_open"] = f"✅ Spreadsheet aperto: '{spreadsheet.title}'"

                    # 5. Lista fogli disponibili
                    worksheets = [ws.title for ws in spreadsheet.worksheets()]
                    checks["worksheets"] = f"📋 Fogli trovati: {worksheets}"

                    # 6. Prova a leggere il foglio "shops"
                    if "shops" in worksheets:
                        ws = spreadsheet.worksheet("shops")
                        records = ws.get_all_records()
                        checks["shops_sheet"] = f"✅ Foglio 'shops' trovato – {len(records)} righe"
                        if records:
                            headers = list(records[0].keys())
                            checks["shops_headers"] = f"📋 Colonne: {headers}"
                            # Mostra i primi shop trovati
                            shop_ids = [str(r.get("shop_id", r.get("id", ""))).strip() for r in records if str(r.get("shop_id", r.get("id", ""))).strip()]
                            checks["shop_ids"] = f"🏪 Shop IDs trovati: {shop_ids}"
                            # Mostra dettagli di ogni negozio
                            for idx, r in enumerate(records):
                                sid = str(r.get("id", "")).strip()
                                if sid:
                                    details = " · ".join(f"{k}: {v}" for k, v in r.items())
                                    checks[f"shop_{idx+1}_{sid}"] = f"🏪 {details}"
                        else:
                            checks["shops_data"] = "⚠️ Il foglio 'shops' è vuoto (nessuna riga dati)"
                    else:
                        checks["shops_sheet"] = f"❌ Foglio 'shops' NON trovato! Fogli disponibili: {worksheets}"

                    # 7. Prova a leggere il foglio "operators"
                    if "operators" in worksheets:
                        ws_op = spreadsheet.worksheet("operators")
                        op_records = ws_op.get_all_records()
                        checks["operators_sheet"] = f"✅ Foglio 'operators' trovato – {len(op_records)} righe"
                        if op_records:
                            op_headers = list(op_records[0].keys())
                            checks["operators_headers"] = f"📋 Colonne: {op_headers}"
                            for idx, r in enumerate(op_records):
                                oid = str(r.get('operator_id', '')).strip()
                                oname = r.get('operator_name', '')
                                osid = str(r.get('shop_id', '')).strip()
                                active = r.get('active', '')
                                cal = r.get('calendar_id', '')
                                checks[f"op_{idx+1}_{oid}"] = (
                                    f"👤 {oname} (shop: {osid}, "
                                    f"active: {active}, calendar: {cal})"
                                )
                        else:
                            checks["operators_data"] = "⚠️ Il foglio 'operators' è vuoto"
                    else:
                        checks["operators_sheet"] = "⚠️ Foglio 'operators' non trovato"

                    # 8. Prova a leggere il foglio "hours"
                    if "hours" in worksheets:
                        ws_hr = spreadsheet.worksheet("hours")
                        hr_records = ws_hr.get_all_records()
                        checks["hours_sheet"] = f"✅ Foglio 'hours' trovato – {len(hr_records)} righe"
                        if hr_records:
                            hr_headers = list(hr_records[0].keys())
                            checks["hours_headers"] = f"📋 Colonne: {hr_headers}"
                            for idx, r in enumerate(hr_records):
                                hsid = str(r.get('shop_id', '')).strip()
                                day = r.get('weekday', '')
                                start = r.get('start', '')
                                end = r.get('end', '')
                                ps = r.get('pause-start', '')
                                pe = r.get('pause-end', '')
                                pause = f", pausa {ps}-{pe}" if ps else ""
                                checks[f"hr_{idx+1}_{hsid}_{day}"] = (
                                    f"🕐 {hsid} · {day}: {start}–{end}{pause}"
                                )
                        else:
                            checks["hours_data"] = "⚠️ Il foglio 'hours' è vuoto"
                    else:
                        checks["hours_sheet"] = "⚠️ Foglio 'hours' non trovato"

                    # 9. Prova a leggere il foglio "services"
                    if "services" in worksheets:
                        ws_sv = spreadsheet.worksheet("services")
                        sv_records = ws_sv.get_all_records()
                        checks["services_sheet"] = f"✅ Foglio 'services' trovato – {len(sv_records)} righe"
                        if sv_records:
                            sv_headers = list(sv_records[0].keys())
                            checks["services_headers"] = f"📋 Colonne: {sv_headers}"
                            for idx, r in enumerate(sv_records):
                                ssid = str(r.get('shop_id', '')).strip()
                                sname = r.get('name', '')
                                dur = r.get('duration', '')
                                price = r.get('price', '')
                                cat = r.get('category', '')
                                active = r.get('active', '')
                                checks[f"svc_{idx+1}_{ssid}"] = (
                                    f"💇 {sname} · {dur}min · €{price} · "
                                    f"cat: {cat} · active: {active} (shop: {ssid})"
                                )
                        else:
                            checks["services_data"] = "⚠️ Il foglio 'services' è vuoto"
                    else:
                        checks["services_sheet"] = "⚠️ Foglio 'services' non trovato"

                    # 10. Prova a leggere il foglio "customers"
                    cust_tab = app.config.get("CUSTOMERS_TAB", "customers")
                    if cust_tab in worksheets:
                        ws_cu = spreadsheet.worksheet(cust_tab)
                        cu_records = ws_cu.get_all_records()
                        checks["customers_sheet"] = f"✅ Foglio '{cust_tab}' trovato – {len(cu_records)} righe"
                        if cu_records:
                            cu_headers = list(cu_records[0].keys())
                            checks["customers_headers"] = f"📋 Colonne: {cu_headers}"
                            for idx, r in enumerate(cu_records[:10]):  # mostra max 10
                                cphone = str(r.get('phone', '')).strip()
                                cname = r.get('customer_name', '')
                                csid = str(r.get('shop_id', '')).strip()
                                visits = r.get('total_visits', 0)
                                checks[f"cust_{idx+1}"] = (
                                    f"👤 {cname or '(no name)'} · "
                                    f"tel: {cphone} · shop: {csid} · visite: {visits}"
                                )
                            if len(cu_records) > 10:
                                checks["customers_more"] = f"… e altri {len(cu_records) - 10} clienti"
                        else:
                            checks["customers_data"] = f"⚠️ Il foglio '{cust_tab}' è vuoto"
                    else:
                        checks["customers_sheet"] = f"⚠️ Foglio '{cust_tab}' non trovato"

                except Exception as e:
                    checks["spreadsheet_open"] = f"❌ Errore apertura spreadsheet: {e}"

            except ImportError as e:
                checks["gspread_auth"] = f"❌ Libreria mancante: {e}"
            except Exception as e:
                checks["gspread_auth"] = f"❌ Errore autenticazione: {e}"

        # 8. Invalida la cache per forzare il refresh
        from services.sheets_service import invalidate_shops_cache
        invalidate_shops_cache()
        checks["cache"] = "🔄 Cache invalidata"

        # Genera HTML
        html = (
            '<div style="font-family:monospace;max-width:700px;margin:40px auto;padding:24px;'
            'background:#1a1a2e;color:#eee;border-radius:12px">'
            '<h2 style="color:#e94560">🔧 Diagnostica Google Sheets</h2>'
        )
        for key, val in checks.items():
            color = "#4caf50" if "✅" in str(val) else "#ff5252" if "❌" in str(val) else "#ffc107"
            html += f'<p style="margin:8px 0"><strong style="color:{color}">{key}:</strong> {val}</p>'
        html += (
            '<hr style="border-color:#333;margin:20px 0">'
            '<p style="color:#999;font-size:12px">⚠️ Rimuovi questo endpoint dopo il debug!</p>'
            '</div>'
        )
        return html

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
