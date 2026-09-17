from datetime import datetime, timedelta

import pytest

from app.db import Database
from app.entry_orders import at_upper_limit, reconcile_entries
from app.kiwoom_service import KiwoomService


NOW = datetime.fromisoformat("2026-09-17T09:00:00+09:00")


class Broker:
    def __init__(self):
        self.filled = 0
        self.remaining = 10
        self.open = True
        self.details_available = True
        self.cancel_calls = []

    def unfilled_buys(self):
        return [{"ord_no": "0000123", "stk_cd": "005930", "oso_qty": str(self.remaining)}] if self.open else []

    def buy_order_details(self, day):
        return [{"ord_no": "0000123", "cntr_qty": str(self.filled), "cntr_uv": "1000",
                 "ord_remnq": str(self.remaining)}] if self.details_available else []

    def stock_info(self, code):
        return {"cur_prc": "+1000", "upl_pric": "+1000"}

    def cancel_buy(self, **kwargs):
        self.cancel_calls.append(kwargs)
        return "0000124"


@pytest.fixture
def database(tmp_path):
    db = Database.__new__(Database)
    db.path = tmp_path / "orders.db"
    db.initialize()
    db.save_pending_entry({
        "stock_code": "005930", "stock_name": "test", "exchange": "SOR", "quantity": 10,
        "average_price": 1000, "stop_loss_rate": 1, "level": 90, "position_rate": 45,
        "nxt_enabled": 0, "opened_at": NOW.isoformat(), "order_no": "0000123",
    })
    return db


def test_cancels_remainder_immediately_and_waits_for_confirmation(database):
    broker = Broker()
    reconcile_entries(database, broker, NOW, allow_cancel=True)
    reconcile_entries(database, broker, NOW + timedelta(seconds=59), allow_cancel=True)
    assert len(broker.cancel_calls) == 1
    assert database.positions() == []
    reconcile_entries(database, broker, NOW + timedelta(seconds=60), allow_cancel=True)
    reconcile_entries(database, broker, NOW + timedelta(seconds=61), allow_cancel=True)
    assert len(broker.cancel_calls) == 1
    assert broker.cancel_calls[0]["order_no"] == "0000123"
    assert len(database.pending_entries()) == 1
    broker.remaining = 0
    broker.open = False
    reconcile_entries(database, broker, NOW + timedelta(seconds=62), allow_cancel=True)
    assert database.pending_entries() == []
    assert database.positions() == []


def test_partial_fill_is_kept_when_remainder_is_cancelled(database):
    broker = Broker()
    broker.filled, broker.remaining = 4, 6
    reconcile_entries(database, broker, NOW, allow_cancel=True)
    assert database.positions()[0]["quantity"] == 4
    reconcile_entries(database, broker, NOW + timedelta(seconds=60), allow_cancel=True)
    broker.remaining, broker.open = 0, False
    reconcile_entries(database, broker, NOW + timedelta(seconds=61), allow_cancel=True)
    assert database.pending_entries() == []
    assert database.positions()[0]["quantity"] == 4


def test_missing_broker_details_do_not_release_reserved_slot(database):
    broker = Broker()
    broker.details_available, broker.open = False, False
    reconcile_entries(database, broker, NOW, allow_cancel=True)
    assert len(database.pending_entries()) == 1
    assert database.positions() == []


def test_disarmed_engine_does_not_cancel(database):
    broker = Broker()
    reconcile_entries(database, broker, NOW, allow_cancel=False)
    reconcile_entries(database, broker, NOW + timedelta(seconds=61), allow_cancel=False)
    assert broker.cancel_calls == []


def test_legacy_unfilled_position_is_moved_to_pending(database):
    pending = database.pending_entries()[0]
    database.remove_pending_entry(pending["stock_code"])
    database.upsert_position(pending)
    reconcile_entries(database, Broker(), NOW, allow_cancel=False)
    assert database.positions() == []
    assert len(database.pending_entries()) == 1


def test_uses_exchange_upper_price_instead_of_rounded_percentage():
    assert at_upper_limit({"cur_prc": "+12890", "upl_pric": "+12890", "flu_rt": "29.94"})
    assert not at_upper_limit({"cur_prc": "+12880", "upl_pric": "+12890"})
    assert not at_upper_limit({"cur_prc": "+12890"})


def test_cancel_requests_only_remaining_quantity_of_original_order(monkeypatch):
    broker = KiwoomService()
    calls = []
    def fetch(api, path, body):
        calls.append((api, path, body))
        return {"ord_no": "0000124"}
    monkeypatch.setattr(broker, "fetch", fetch)
    assert broker.cancel_buy(exchange="SOR", code="005930", order_no="0000123") == "0000124"
    assert calls == [("kt10003", "/api/dostk/ordr", {
        "dmst_stex_tp": "SOR", "orig_ord_no": "0000123", "stk_cd": "005930", "cncl_qty": "0",
    })]


def test_cancel_request_survives_database_reload_without_duplicate_cancel(database):
    broker = Broker()
    reconcile_entries(database, broker, NOW, allow_cancel=True)
    reloaded = Database.__new__(Database)
    reloaded.path = database.path
    reloaded.initialize()
    reconcile_entries(reloaded, broker, NOW + timedelta(seconds=60), allow_cancel=True)
    assert len(broker.cancel_calls) == 1


def test_buy_uses_market_ioc_without_resting_order_fallback(monkeypatch):
    broker = KiwoomService()
    calls = []
    def fetch(api, path, body):
        calls.append((api, body))
        return {"ord_no": "321"}
    monkeypatch.setattr(broker, "fetch", fetch)
    assert broker.buy_market(exchange="SOR", code="005930", quantity=10) == "321"
    assert len(calls) == 1
    assert calls[0][0] == "kt10000"
    assert calls[0][1]["trde_tp"] == "13"
