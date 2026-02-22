"""Test per il bot flow."""

import pytest
from app import create_app


@pytest.fixture
def app_ctx():
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        yield app


def test_get_shop_by_id(app_ctx):
    """get_shop_by_id deve trovare i demo shop."""
    from services.sheets_service import get_shop_by_id
    shop = get_shop_by_id("demo1")
    assert shop is not None
    assert shop["name"] == "Barbiere Da Mario"


def test_get_shop_by_id_not_found(app_ctx):
    """get_shop_by_id con ID inesistente deve restituire None."""
    from services.sheets_service import get_shop_by_id
    shop = get_shop_by_id("non_esiste")
    assert shop is None
