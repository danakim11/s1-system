from datetime import datetime

from app.db import Database
from app.models import StrategySettings


def test_history_keeps_qualified_daily_records_after_candidate_drops_out(tmp_path):
    db = Database.__new__(Database)
    db.path = tmp_path / "history.db"
    db.initialize()
    settings = StrategySettings()
    candidate = {"stock_code": "051160", "stock_name": "test", "allowed": True, "price": 100}
    db.save_candidates([candidate], settings, datetime.fromisoformat("2026-09-17T00:00:00+00:00"))
    db.save_candidates([{**candidate, "price": 110}], settings, datetime.fromisoformat("2026-09-17T10:00:00+09:00"))
    db.save_candidates([{**candidate, "allowed": False}], settings, datetime.fromisoformat("2026-09-17T11:00:00+09:00"))
    rows = db.candidate_history("2026-09-17")
    assert len(rows) == 1
    assert rows[0]["first_seen"] == "2026-09-17T09:00:00+09:00"
    assert rows[0]["last_seen"] == "2026-09-17T10:00:00+09:00"
    assert rows[0]["price"] == 110
    assert rows[0]["settings"]["entry_rate"] == 29
    db.save_candidates([candidate], settings, datetime.fromisoformat("2026-09-17T15:01:00+00:00"))
    assert len(db.candidate_history("2026-09-18")) == 1
    assert db.candidate_history("2026-09-16") == []
    db.initialize()
    assert len(db.candidate_history("2026-09-17")) == 1
