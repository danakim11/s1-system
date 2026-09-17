import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import env
from .db import db
from .kiwoom_service import KiwoomUnavailable, kiwoom
from .models import TradeCreate
from .strategy import candidate_allowed, in_trading_window, parse_number, position_budget

KST = ZoneInfo("Asia/Seoul")


class TradingEngine:
    def __init__(self) -> None:
        self.running = False
        self.armed = False
        self.last_error = ""
        self.last_scan: list[dict] = []
        self._task: asyncio.Task | None = None

    def status(self) -> dict:
        return {
            "running": self.running,
            "armed": self.armed,
            "live_enabled": env.live_trading,
            "last_error": self.last_error,
            "last_scan": self.last_scan,
        }

    def arm(self, phrase: str) -> None:
        if not env.live_trading:
            raise ValueError(".env에서 LIVE_TRADING=true로 설정해야 합니다.")
        if phrase != env.live_confirm_phrase:
            raise ValueError("확인 문구가 일치하지 않습니다.")
        self.armed = True

    def disarm(self) -> None:
        self.armed = False

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        self.running = False
        self.armed = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while self.running:
            settings = db.get_settings()
            try:
                await asyncio.to_thread(self._tick, settings)
                self.last_error = ""
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self.last_error = str(exc)
            await asyncio.sleep(settings.scan_interval_seconds)

    def scan(self) -> list[dict]:
        settings = db.get_settings()
        ranked = kiwoom.trading_value_top()
        candidates: list[dict] = []
        for row in ranked:
            change_rate = parse_number(row.get("flu_rt"))
            turnover_won = parse_number(row.get("trde_prica")) * env.kiwoom_trading_value_unit_won
            if change_rate < settings.entry_rate - 0.5:
                continue
            code = str(row.get("stk_cd", "")).replace("_AL", "").replace("_NX", "")
            info = kiwoom.stock_info(code)
            market_cap_eok = parse_number(info.get("mac"))
            allowed = candidate_allowed(
                change_rate=change_rate, turnover_won=turnover_won,
                market_cap_eok=market_cap_eok, settings=settings,
            )
            candidates.append({
                "stock_code": code,
                "stock_name": row.get("stk_nm", ""),
                "price": parse_number(row.get("cur_prc")),
                "change_rate": change_rate,
                "turnover_eok": round(turnover_won / 100_000_000, 1),
                "market_cap_eok": market_cap_eok,
                "allowed": allowed,
            })
        self.last_scan = candidates
        return candidates

    def _tick(self, settings) -> None:
        self._reconcile_exits()
        self._monitor_positions(settings)
        if not (self.armed and in_trading_window(settings)):
            return
        positions = db.positions()
        if len(positions) >= settings.max_positions:
            return
        for item in self.scan():
            today = datetime.now(KST).date().isoformat()
            if not item["allowed"] or db.is_blocked(item["stock_code"], today):
                continue
            self._enter(item, settings)
            break

    def _enter(self, item: dict, settings) -> None:
        account = kiwoom.account("KRX")
        budget = position_budget(
            account.equity, settings.applied_level, settings.max_positions, account.available_20
        )
        quantity = int(budget // item["price"])
        if quantity < 1:
            raise KiwoomUnavailable("주문 가능 금액이 부족합니다.")
        order_no = kiwoom.buy_market(exchange=settings.exchange, code=item["stock_code"], quantity=quantity)
        detail = kiwoom.stock_detail(item["stock_code"])
        db.upsert_position({
            "stock_code": item["stock_code"], "stock_name": item["stock_name"],
            "exchange": settings.exchange, "quantity": quantity, "average_price": item["price"],
            "stop_loss_rate": settings.stop_loss_rate, "level": settings.applied_level,
            "nxt_enabled": 1 if str(detail.get("nxtEnable", "")).upper() in {"Y", "1", "TRUE"} else 0,
            "opened_at": datetime.now(KST).isoformat(), "order_no": order_no,
        })
        db.block_entry(item["stock_code"], datetime.now(KST).date().isoformat())

    def _monitor_positions(self, settings) -> None:
        now = datetime.now(KST)
        account_positions = kiwoom.account("KRX").positions if db.positions() else []
        for position in db.positions():
            actual = next(
                (row for row in account_positions if str(row.get("stk_cd", "")).replace("A", "", 1) == position["stock_code"]),
                None,
            )
            if actual and parse_number(actual.get("buy_uv")):
                position["average_price"] = parse_number(actual.get("buy_uv"))
                position["quantity"] = int(parse_number(actual.get("cur_qty"))) or position["quantity"]
                db.upsert_position(position)
            info = kiwoom.stock_info(position["stock_code"])
            current = parse_number(info.get("cur_prc"))
            stop = position["average_price"] * (1 - position["stop_loss_rate"] / 100)
            opened = datetime.fromisoformat(position["opened_at"])
            exit_reason = None
            if current and current <= stop:
                exit_reason = "STOP_LOSS"
            elif now.date() > opened.date() and self._exit_session_open(position, settings, now):
                exit_reason = "NEXT_DAY_OPEN"
            if exit_reason:
                self._exit(position, exit_reason, settings)

    @staticmethod
    def _exit_session_open(position: dict, settings, now: datetime) -> bool:
        mode = settings.exit_session
        use_nxt = mode == "NXT" or (mode == "AUTO" and position.get("nxt_enabled"))
        target = now.replace(hour=8 if use_nxt else 9, minute=0, second=0, microsecond=0)
        return now >= target

    def _exit(self, position: dict, reason: str, settings) -> None:
        use_nxt = settings.exit_session == "NXT" or (
            settings.exit_session == "AUTO" and position.get("nxt_enabled") and reason == "NEXT_DAY_OPEN"
        )
        exchange = "NXT" if use_nxt else "KRX"
        order_no = kiwoom.sell_market(exchange=exchange, code=position["stock_code"], quantity=position["quantity"])
        db.add_pending_exit({
            "stock_code": position["stock_code"], "stock_name": position["stock_name"],
            "quantity": position["quantity"], "entry_price": position["average_price"],
            "stop_loss_rate": position["stop_loss_rate"], "level": position["level"],
            "entered_at": position["opened_at"], "exit_reason": reason,
            "order_no": order_no, "submitted_at": datetime.now(KST).isoformat(),
        })
        db.remove_position(position["stock_code"])

    def _reconcile_exits(self) -> None:
        pending = db.pending_exits()
        if not pending:
            return
        by_date: dict[str, list[dict]] = {}
        for item in pending:
            key = datetime.fromisoformat(item["submitted_at"]).strftime("%Y%m%d")
            by_date.setdefault(key, []).append(item)
        for base_date, items in by_date.items():
            journal = kiwoom.today_trade_journal(base_date)
            for item in items:
                fill = next(
                    (row for row in journal if str(row.get("stk_cd", "")).replace("A", "", 1) == item["stock_code"] and parse_number(row.get("sell_qty")) >= item["quantity"]),
                    None,
                )
                if not fill or not parse_number(fill.get("sel_avg_pric")):
                    continue
                db.add_trade(TradeCreate(
                    stock_code=item["stock_code"], stock_name=item["stock_name"],
                    entry_price=item["entry_price"], exit_price=parse_number(fill.get("sel_avg_pric")),
                    quantity=item["quantity"], exit_reason=item["exit_reason"], level=item["level"],
                    stop_loss_rate=item["stop_loss_rate"], entered_at=datetime.fromisoformat(item["entered_at"]),
                    exited_at=datetime.fromisoformat(item["submitted_at"]),
                ))
                db.remove_pending_exit(item["stock_code"])


engine = TradingEngine()
