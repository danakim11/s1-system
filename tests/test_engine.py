import asyncio

import pytest

from app.engine import TradingEngine


def test_start_without_event_loop_does_not_report_running():
    engine = TradingEngine()
    with pytest.raises(RuntimeError):
        engine.start()
    assert not engine.status()["running"]
    assert not engine.status()["loop_active"]
    assert engine.last_error


def test_loop_failure_is_visible_and_can_restart():
    async def scenario():
        engine = TradingEngine()

        async def fail():
            raise RuntimeError("settings unavailable")

        engine._loop = fail
        engine.start()
        engine.armed = True
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not engine.running
        assert not engine.armed
        assert not engine.status()["loop_active"]
        assert "settings unavailable" in engine.last_error

        started = asyncio.Event()

        async def wait():
            started.set()
            await asyncio.Event().wait()

        engine._loop = wait
        engine.start()
        await started.wait()
        task = engine._task
        engine.start()
        assert engine._task is task
        assert engine.status()["loop_active"]
        assert engine.running
        assert not engine.last_error
        engine.stop()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not engine.running
        assert not engine.status()["loop_active"]
        assert not engine.last_error

    asyncio.run(scenario())


def test_start_endpoint_runs_background_task(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from threading import Event

    engine = TradingEngine()
    started = Event()

    async def wait():
        started.set()
        await asyncio.Event().wait()

    engine._loop = wait
    monkeypatch.setattr(main, "engine", engine)
    with TestClient(main.app) as client:
        response = client.post("/api/engine/start")
        assert response.status_code == 200
        assert response.json()["loop_active"]
        assert started.wait(timeout=2)
        assert client.get("/api/engine/status").json()["loop_active"]
        assert client.post("/api/engine/stop").status_code == 200
