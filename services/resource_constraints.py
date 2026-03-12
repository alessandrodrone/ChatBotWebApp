"""
Gestione vincoli risorse fisiche per prenotazioni.
"""
import logging
from flask import current_app

log = logging.getLogger(__name__)

_constraints_cache = None

def get_resource_constraints() -> dict:
    """Legge il foglio 'resource_constraints' e restituisce dict:
    {shop_id: {resource_name: max_concurrent}}
    """
    global _constraints_cache
    if _constraints_cache is not None:
        return _constraints_cache
    try:
        from services.sheets_service import _get_spreadsheet
        ss = _get_spreadsheet()
        if ss is None:
            _constraints_cache = {}
            return _constraints_cache
        ws = ss.worksheet("resource_constraints")
        records = ws.get_all_records()
        _constraints_cache = {}
        for row in records:
            sid = str(row.get("shop_id", "")).strip()
            res = str(row.get("resource_name", "")).strip().lower()
            maxc = int(row.get("max_concurrent", 1) or 1)
            if sid and res:
                _constraints_cache.setdefault(sid, {})[res] = maxc
        return _constraints_cache
    except Exception as e:
        log.warning("Errore lettura resource_constraints: %s", e)
        _constraints_cache = {}
        return _constraints_cache


def invalidate_resource_constraints_cache():
    global _constraints_cache
    _constraints_cache = None
