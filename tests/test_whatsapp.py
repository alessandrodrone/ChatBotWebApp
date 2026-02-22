"""Test per il webhook WhatsApp."""

import json
import pytest
from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_webhook_verify_ok(client):
    """GET /webhook con token corretto deve restituire il challenge."""
    resp = client.get("/webhook", query_string={
        "hub.mode": "subscribe",
        "hub.verify_token": "risponditu_verify_2026",
        "hub.challenge": "test_challenge_123",
    })
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "test_challenge_123"


def test_webhook_verify_fail(client):
    """GET /webhook con token sbagliato deve restituire 403."""
    resp = client.get("/webhook", query_string={
        "hub.mode": "subscribe",
        "hub.verify_token": "token_sbagliato",
        "hub.challenge": "test",
    })
    assert resp.status_code == 403


def test_webhook_post_start(client):
    """POST /webhook con START_demo1 deve restituire 200."""
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "metadata": {"phone_number_id": "123"},
                    "contacts": [{"profile": {"name": "Mario Rossi"}}],
                    "messages": [{
                        "from": "393331234567",
                        "type": "text",
                        "text": {"body": "START_demo1"},
                    }],
                }
            }]
        }]
    }
    resp = client.post("/webhook", json=payload)
    assert resp.status_code == 200
