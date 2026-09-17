from app.db import Database


def test_curve_includes_all_trades_in_exit_order(tmp_path):
    database = Database.__new__(Database)
    database.path = tmp_path / "curve.db"
    database.initialize()
    with database.connect() as conn:
        for i in range(205):
            conn.execute(
                "INSERT INTO trades(stock_code,stock_name,entry_price,exit_price,quantity,exit_reason,"
                "level,stop_loss_rate,entered_at,exited_at,return_pct,realized_pnl) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("005930", "test", 100, 110, 1, "MANUAL", 10, 1,
                 "2026-01-01T09:00:00", "2026-01-01T10:00:00", 10, 10),
            )
        conn.execute(
            "UPDATE trades SET exited_at='2025-12-31T10:00:00',realized_pnl=-20 WHERE id=205"
        )
    curve = database.equity_curve()
    assert len(curve) == 205
    assert curve[0]["id"] == 205
    assert curve[0]["cumulative_pnl"] == -20
    assert curve[-1]["cumulative_pnl"] == 2020
