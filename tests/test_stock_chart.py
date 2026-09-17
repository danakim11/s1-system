from fastapi.testclient import TestClient

from app import main


def test_chart_is_read_only_sorted_and_limited_to_selected_date(monkeypatch):
    def fetch(api_id, path, body):
        assert api_id == "ka10081"
        assert path == "/api/dostk/chart"
        assert body == {"stk_cd": "051160", "base_dt": "20260917", "upd_stkpc_tp": "1"}
        return {"stk_dt_pole_chart_qry": [
            {"dt": day, "open_pric": "+100", "high_pric": "+120", "low_pric": "-90", "cur_prc": "+110", "trde_qty": "500"}
            for day in ["20260918", "20260917", "20260916"]
        ]}
    monkeypatch.setattr(main.kiwoom, "fetch", fetch)
    with TestClient(main.app) as client:
        response = client.get("/api/stock-chart?code=051160&base_date=2026-09-17")
        assert response.status_code == 200
        assert [p["date"] for p in response.json()] == ["2026-09-16", "2026-09-17"]
        assert response.json()[0]["low"] == 90
        assert client.get("/api/stock-chart?code=invalid&base_date=bad").status_code == 422
