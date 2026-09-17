from types import SimpleNamespace

from app import engine as engine_module
from app.engine import TradingEngine
from app.kiwoom_service import KiwoomService
from app.models import StrategySettings


def test_display_and_order_scan_fetch_all_pages(monkeypatch):
    first = {"stk_cd": "042370", "stk_nm": "first"}
    later = {"stk_cd": "051160", "stk_nm": "later"}
    def pages(**kwargs):
        assert kwargs["max_pages"] == 0
        return iter([SimpleNamespace(body={"trde_prica_upper": [first]}),
                     SimpleNamespace(body={"trde_prica_upper": [later]})])
    service = KiwoomService()
    monkeypatch.setattr(service, "_client", lambda: SimpleNamespace(iterate_pages=pages))
    monkeypatch.setattr(service, "fetch", lambda *args: {"trde_prica_upper": [first]})
    assert service.candidate_display_rows() == [first, later]
    assert service.trading_value_top() == [first, later]
    checks = []
    assert service.trading_value_top(on_page=lambda: checks.append(True)) == [first, later]
    assert len(checks) == 2


def test_position_checks_run_during_ranking_scan(monkeypatch):
    scanner = TradingEngine()
    scanner.running = True
    checked = []
    clock = iter([0, 2, 2, 4, 4])
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(engine_module.db, "get_settings", lambda: StrategySettings())
    monkeypatch.setattr(engine_module.db, "save_candidates", lambda *args: None)
    monkeypatch.setattr(scanner, "_monitor_positions", lambda settings: checked.append(True))
    def fetch(min_rate, **kwargs):
        kwargs["on_page"]()
        kwargs["on_page"]()
        assert len(checked) == 2
        return []
    monkeypatch.setattr(engine_module.kiwoom, "near_entry_rows", fetch)
    assert scanner.scan() == []


def test_display_includes_low_turnover_candidates_and_excludes_falling_stocks(monkeypatch):
    rows = [
        {"stk_cd": "042370_AL", "stk_nm": "first", "flu_rt": "+30", "cur_prc": "+11440", "trde_prica": "72176"},
        {"stk_cd": "051160_AL", "stk_nm": "second", "flu_rt": "+29.94", "cur_prc": "+12890", "trde_prica": "24360"},
        {"stk_cd": "478340_AL", "stk_nm": "third", "flu_rt": "+29.92", "cur_prc": "+13850", "trde_prica": "18826"},
        {"stk_cd": "000001_AL", "stk_nm": "falling", "flu_rt": "-30", "cur_prc": "-1000", "trde_prica": "50000"},
    ]
    monkeypatch.setattr(engine_module.db, "get_settings", lambda: StrategySettings())
    monkeypatch.setattr(engine_module.kiwoom, "near_entry_rows", lambda *args, **kwargs: rows)
    monkeypatch.setattr(engine_module.kiwoom, "today_turnover", lambda code: next(float(r["trde_prica"])*1_000_000 for r in rows if r["stk_cd"].startswith(code)))
    monkeypatch.setattr(engine_module.kiwoom, "stock_info", lambda code: {"mac": "2000"})
    scanner = TradingEngine()
    display = scanner.scan(display_only=True)
    assert [r["stock_code"] for r in display] == ["042370", "051160", "478340"]
    assert [r["allowed"] for r in display] == [True, False, False]
    assert display[1]["reason"] == "거래대금 미달"
    assert display[2]["turnover_eok"] == 188.3
    assert scanner.last_scan == []
    assert [r["stock_code"] for r in scanner.scan()] == ["042370", "051160", "478340"]


def test_fast_scan_continues_beyond_first_page_until_below_threshold(monkeypatch):
    pages = [
        [{"stk_cd": str(i), "flu_rt": "29.9"} for i in range(100)],
        [{"stk_cd": "later", "flu_rt": "29.0"}, {"stk_cd": "below", "flu_rt": "27.0"}],
    ]
    def iterate(**kwargs):
        assert kwargs["body"]["mrkt_tp"] == "000"
        assert kwargs["body"]["sort_tp"] == "1"
        assert kwargs["max_pages"] == 0
        for page in pages:
            yield SimpleNamespace(body={"pred_pre_flu_rt_upper": page})
        raise AssertionError("Should stop once sorted prices are below the threshold")
    broker = KiwoomService()
    monkeypatch.setattr(broker, "_client", lambda: SimpleNamespace(iterate_pages=iterate))
    rows = broker.near_entry_rows(28.5)
    assert len(rows) == 101
    assert rows[-1]["stk_cd"] == "later"


def test_unlocked_limit_up_is_excluded_for_rest_of_day_and_after_restart(tmp_path, monkeypatch):
    from app.db import Database
    from datetime import datetime
    database = Database.__new__(Database)
    database.path = tmp_path / "exclude.db"
    database.initialize()
    monkeypatch.setattr(engine_module, "db", database)
    assert TradingEngine._exclude_unlocked("051160", {"upl_pric": "12890", "high_pric": "12890", "cur_prc": "12880"})
    database.initialize()
    assert TradingEngine._exclude_unlocked("051160", {"upl_pric": "12890", "high_pric": "12890", "cur_prc": "12890"})
    assert not TradingEngine._exclude_unlocked("000001", {"upl_pric": "1000", "high_pric": "990", "cur_prc": "990"})
    assert not database.is_candidate_excluded("051160", "2099-01-01")


def test_intraday_history_detects_unlock_even_after_relocking(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    day = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    bars = [
        {"cntr_tm": day+"090200", "high_pric": "100", "low_pric": "99", "cur_prc": "100"},
        {"cntr_tm": day+"090100", "high_pric": "100", "low_pric": "95", "cur_prc": "100"},
    ]
    service = KiwoomService()
    monkeypatch.setattr(service, "_client", lambda: SimpleNamespace(iterate_pages=lambda **kwargs: iter([
        SimpleNamespace(body={"stk_min_pole_chart_qry": bars})])))
    assert service.had_unlocked_limit("005930", 100)
    service = KiwoomService()
    monkeypatch.setattr(service, "_client", lambda: SimpleNamespace(iterate_pages=lambda **kwargs: iter([
        SimpleNamespace(body={"stk_min_pole_chart_qry": bars[1:]})])))
    assert not service.had_unlocked_limit("005930", 100)
