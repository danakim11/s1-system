from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .db import db
from .engine import engine
from .kiwoom_service import KiwoomUnavailable
from .models import StrategySettings, TradeCreate
from .strategy import recommend_level, summarize

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
    }


@app.put("/api/settings")
def update_settings(settings: StrategySettings):
    db.save_settings(settings)
    return settings


@app.post("/api/trades")
def add_trade(trade: TradeCreate):
    return {"id": db.add_trade(trade)}


@app.post("/api/scan")
def scan():
    try:
        return engine.scan()
    except KiwoomUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/engine/start")
async def start_engine():
    engine.start()
    return engine.status()


@app.post("/api/engine/stop")
def stop_engine():
    engine.stop()
    return engine.status()


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
