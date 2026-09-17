from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .db import db
from .engine import engine
from .kiwoom_service import KiwoomUnavailable, kiwoom
from .models import StrategySettings, TradeCreate
from .strategy import parse_number, recommend_level, summarize

app = FastAPI(title="S1 System", version="0.1.0")
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def index():
    return FileResponse(static_dir / "index.html")


@app.get("/api/dashboard")
def dashboard():
    rows = db.trades()
    today_rows = [row for row in rows if row["exited_at"].startswith(date.today().isoformat())]
    recommended, reason = recommend_level(rows)
    return {
        "settings": db.get_settings().model_dump(),
        "engine": engine.status(),
        "today": summarize(today_rows).__dict__,
        "recent5": summarize(rows[-5:]).__dict__,
        "recent10": summarize(rows[-10:]).__dict__,
        "recent20": summarize(rows[-20:]).__dict__,
        "recommended_level": recommended,
        "recommendation_reason": reason,
        "trades": rows[-50:],
        "positions": db.positions(),
        "pending_entries": db.pending_entries(),
    }


@app.put("/api/settings")
def update_settings(settings: StrategySettings):
    db.save_settings(settings)
    return settings


@app.get("/api/equity-curve")
def equity_curve():
    return db.equity_curve()


@app.get("/api/candidate-history")
def candidate_history(trade_date: date):
    return db.candidate_history(trade_date.isoformat())


@app.get("/api/stock-chart")
def stock_chart(code: str = Query(pattern=r"^[0-9A-Z]{6}$"), base_date: date = Query()):
    try:
        payload = kiwoom.fetch("ka10081", "/api/dostk/chart", {
            "stk_cd": code, "base_dt": base_date.strftime("%Y%m%d"), "upd_stkpc_tp": "1",
        })
    except KiwoomUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    points = []
    for row in payload.get("stk_dt_pole_chart_qry", []):
        day = str(row.get("dt", ""))
        if len(day) != 8 or not day.isdigit() or day > base_date.strftime("%Y%m%d"):
            continue
        point = {"date": f"{day[:4]}-{day[4:6]}-{day[6:]}",
                 "open": parse_number(row.get("open_pric")), "high": parse_number(row.get("high_pric")),
                 "low": parse_number(row.get("low_pric")), "close": parse_number(row.get("cur_prc")),
                 "volume": parse_number(row.get("trde_qty"))}
        if min(point[k] for k in ("open", "high", "low", "close")) > 0:
            points.append(point)
    return sorted(points, key=lambda point: point["date"])[-60:]


@app.post("/api/trades")
def add_trade(trade: TradeCreate):
    return {"id": db.add_trade(trade)}


@app.post("/api/scan")
def scan():
    try:
        return engine.scan(display_only=True)
    except KiwoomUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/engine/start")
async def start_engine():
    engine.start()
    return engine.status()


@app.post("/api/engine/stop")
async def stop_engine():
    engine.stop()
    return engine.status()


@app.get("/api/engine/status")
async def engine_status():
    return engine.status()


@app.get("/api/order-state")
def order_state():
    return {"positions": db.positions(), "pending_entries": db.pending_entries()}


@app.post("/api/engine/arm")
def arm_engine():
    try:
        engine.arm()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return engine.status()


@app.post("/api/engine/disarm")
def disarm_engine():
    engine.disarm()
    return engine.status()
