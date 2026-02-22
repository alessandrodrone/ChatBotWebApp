"""Test per la landing page dinamica."""

import pytest
from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_landing_demo_shop(client):
    """La landing di uno shop demo deve restituire 200."""
    resp = client.get("/s/demo1")
    assert resp.status_code == 200
    assert "Barbiere Da Mario" in resp.get_data(as_text=True)


def test_landing_contains_wa_link(client):
    """La landing deve contenere il link wa.me con START_."""
    resp = client.get("/s/demo1")
    html = resp.get_data(as_text=True)
    assert "wa.me" in html
    assert "START_demo1" in html


def test_landing_404_unknown_shop(client):
    """Uno shop inesistente deve restituire 404."""
    resp = client.get("/s/inesistente_xyz")
    assert resp.status_code == 404


def test_landing_all_demo_shops(client):
    """Verifica che tutti i demo shop funzionino."""
    for sid in ("demo1", "demo2", "demo3"):
        resp = client.get(f"/s/{sid}")
        assert resp.status_code == 200
