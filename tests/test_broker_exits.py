from datetime import datetime
from types import SimpleNamespace

from app.broker_exits import reconcile_broker_exits
from app.db import Database


def test_external_partial_and_full_sale_are_recorded_once(tmp_path):
    db = Database.__new__(Database)
    db.path = tmp_path / "external.db"
    db.initialize()
    db.upsert_position({"stock_code": "051160", "stock_name": "test", "exchange": "SOR", "quantity": 10,
                        "average_price": 1000, "stop_loss_rate": 3, "level": 90, "position_rate": 45,
                        "nxt_enabled": 0, "opened_at": "2026-09-17T09:00:00+09:00", "order_no": "123"})
    fill = {"stk_cd": "A051160", "ord_no": "456", "cntr_qty": "4", "cntr_uv": "950", "ord_tm": "10:00:00"}
    broker = SimpleNamespace(sell_order_details=lambda day: [dict(fill)])
    now = datetime.fromisoformat("2026-09-17T10:01:00+09:00")
    holdings = [{"stk_cd": "A051160", "cur_qty": "6"}]
    reconcile_broker_exits(db, broker, holdings, now)
    reconcile_broker_exits(db, broker, holdings, now)
    assert len(db.trades()) == 1
    assert db.positions()[0]["quantity"] == 6
    assert db.trades()[0]["exit_reason"] == "BROKER_EXIT"
    fill.update(cntr_qty="10", cntr_uv="920")
    reconcile_broker_exits(db, broker, [], now)
    reconcile_broker_exits(db, broker, [], now)
    assert db.positions() == []
    assert sum(row["quantity"] for row in db.trades()) == 10
    assert sum(row["realized_pnl"] for row in db.trades()) == -800


def test_missing_holdings_without_sell_evidence_does_not_fabricate_a_trade(tmp_path):
    db = Database.__new__(Database)
    db.path = tmp_path / "missing.db"
    db.initialize()
    db.upsert_position({"stock_code": "051160", "stock_name": "test", "exchange": "SOR", "quantity": 10,
                        "average_price": 1000, "stop_loss_rate": 3, "level": 90, "position_rate": 45,
                        "nxt_enabled": 0, "opened_at": "2026-09-17T09:00:00+09:00", "order_no": "123"})
    reconcile_broker_exits(db, SimpleNamespace(sell_order_details=lambda day: []), [],
                          datetime.fromisoformat("2026-09-17T10:00:00+09:00"))
    assert db.trades() == []
    assert db.positions()[0]["quantity"] == 10
