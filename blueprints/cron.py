"""
Blueprint – Cron / Health-check.
Utile per keep-alive su Railway o task schedulati.
"""

from flask import Blueprint, jsonify

cron_bp = Blueprint("cron", __name__)


@cron_bp.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200
