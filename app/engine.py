import asyncio
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import env
from .db import db
from .broker_exits import reconcile_broker_exits
from .entry_orders import at_upper_limit, reconcile_entries, request_cancel
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
            "loop_active": self._task is not None and not self._task.done(),
            "armed": self.armed,
            "live_enabled": env.live_trading,
            "last_error": self.last_error,
            "last_scan": self.last_scan,
        }

    def arm(self) -> None:
        if not env.live_trading:
            raise ValueError(".env에서 LIVE_TRADING=true로 설정해야 합니다.")
        self.armed = True

    def disarm(self) -> None:
        self.armed = False

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.running = False
            self.armed = False
            self.last_error = "감시 루프를 시작할 이벤트 루프가 없습니다."
            raise
        self._task = loop.create_task(self._loop())
        self._task.add_done_callback(self._loop_done)
        self.running = True
        self.last_error = ""

    def _loop_done(self, task: asyncio.Task) -> None:
        error = None if task.cancelled() else task.exception()
        if task is not self._task:
            return
        if self.running:
            self.last_error = f"감시 루프가 종료되었습니다: {error}" if error else "감시 루프가 예기치 않게 종료되었습니다."
        self.running = False
        self.armed = False

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
            await asyncio.sleep(min(1, settings.scan_interval_seconds))

    def scan(self, *, display_only: bool = False) -> list[dict]:
        settings = db.get_settings()
        last_check = time.monotonic()
        def check_positions():
            nonlocal last_check
            if self.running and time.monotonic() - last_check >= 1:
                self._monitor_positions(db.get_settings())
                last_check = time.monotonic()
        ranked = kiwoom.near_entry_rows(settings.entry_rate - 0.5, on_page=None if display_only else check_positions)
        candidates: list[dict] = []
        for row in ranked:
            if not display_only:
                check_positions()
            change_rate = parse_number(row.get("flu_rt"), absolute=False)
            if change_rate < settings.entry_rate - 0.5:
                continue
            code = str(row.get("stk_cd", "")).replace("_AL", "").replace("_NX", "")
            if db.is_candidate_excluded(code, datetime.now(KST).date().isoformat()):
                continue
            info = kiwoom.stock_info(code)
            if self._exclude_unlocked(code, info):
                continue
            change_rate = parse_number(info.get("flu_rt", row.get("flu_rt")), absolute=False)
            if change_rate < settings.entry_rate - 0.5:
                continue
            if not display_only and (at_upper_limit(info) or parse_number(info.get("mac")) < settings.min_market_cap_eok):
                continue
            turnover_won = kiwoom.today_turnover(code)
            market_cap_eok = parse_number(info.get("mac"))
            allowed = candidate_allowed(
                change_rate=change_rate, turnover_won=turnover_won,
                market_cap_eok=market_cap_eok, settings=settings,
            )
            reasons = []
            if change_rate < settings.entry_rate:
                reasons.append("등락률 미달")
            if turnover_won < settings.min_turnover_eok * 100_000_000:
                reasons.append("거래대금 미달")
            if market_cap_eok < settings.min_market_cap_eok:
                reasons.append("시가총액 미달")
            candidates.append({
                "stock_code": code,
                "stock_name": row.get("stk_nm", ""),
                "price": parse_number(info.get("cur_prc", row.get("cur_prc"))),
                "change_rate": change_rate,
                "turnover_eok": round(turnover_won / 100_000_000, 1),
                "market_cap_eok": market_cap_eok,
                "allowed": allowed,
                "reason": " · ".join(reasons) if reasons else "조건 충족",
            })
        db.save_candidates(candidates, settings, datetime.now(KST))
        if not display_only:
            self.last_scan = candidates
        return candidates

    def _tick(self, settings) -> None:
        self._monitor_positions(settings)
        reconcile_entries(db, kiwoom, datetime.now(KST), allow_cancel=self.running and self.armed)
        self._reconcile_exits()
        if not (self.armed and in_trading_window(settings)):
            return
        occupied = {p["stock_code"] for p in db.positions() + db.pending_entries()}
        if len(occupied) >= settings.max_positions:
            return
        for item in self.scan():
            today = datetime.now(KST).date().isoformat()
            if item["stock_code"] in occupied or not item["allowed"] or db.is_blocked(item["stock_code"], today):
                continue
            if self._enter(item, settings):
                break

    def _enter(self, item: dict, settings) -> bool:
        info = kiwoom.stock_info(item["stock_code"])
        if self._exclude_unlocked(item["stock_code"], info):
            return False
        # 이미 상한가에 도달한 종목에 새 매수 대기 주문을 쌓지 않는다.
        if at_upper_limit(info):
            return False
        current_price = parse_number(info.get("cur_prc"))
        if current_price <= 0 or not candidate_allowed(
            change_rate=parse_number(info.get("flu_rt"), absolute=False),
            turnover_won=kiwoom.today_turnover(item["stock_code"]),
            market_cap_eok=parse_number(info.get("mac")), settings=settings,
        ):
            return False
        account = kiwoom.account("KRX")
        budget = position_budget(
            account.equity, settings.applied_level, settings.max_positions, account.available_20
        )
        if item["price"] <= 0:
            raise KiwoomUnavailable(f"{item['stock_code']} 현재가를 확인할 수 없습니다.")
        quantity = int(budget // current_price)
        if quantity < 1:
            raise KiwoomUnavailable(
                f"주문 가능 금액이 부족합니다: {item['stock_code']} "
                f"1주 {item['price']:,.0f}원, 종목 예산 {budget:,.0f}원 "
                f"(추정자산 {account.equity:,.0f}원 × {settings.applied_level}% "
                f"÷ {settings.max_positions}종목, 주문 한도 {account.available_20:,.0f}원)."
            )
        detail = kiwoom.stock_detail(item["stock_code"])
        if not (self.running and self.armed):
            return False
        pending = {
            "stock_code": item["stock_code"], "stock_name": item["stock_name"],
            "exchange": settings.exchange, "quantity": quantity, "average_price": current_price,
            "stop_loss_rate": settings.stop_loss_rate, "level": settings.applied_level,
            "position_rate": round(settings.applied_level / settings.max_positions, 2),
            "nxt_enabled": 1 if str(detail.get("nxtEnable", "")).upper() in {"Y", "1", "TRUE"} else 0,
            "opened_at": datetime.now(KST).isoformat(), "order_no": "",
            "filled_quantity": 0, "remaining_quantity": quantity, "status": "주문 응답 확인 대기",
        }
        db.save_pending_entry(pending)
        db.block_entry(item["stock_code"], datetime.now(KST).date().isoformat())
        try:
            order_no = kiwoom.buy_market(exchange=settings.exchange, code=item["stock_code"], quantity=quantity)
        except KiwoomUnavailable:
            # 증권사가 명시적으로 거절한 주문은 체결 대기 자리를 차지하지 않는다.
            db.remove_pending_entry(item["stock_code"])
            raise
        if not order_no:
            raise KiwoomUnavailable("매수 주문번호 확인 실패: 주문 내역을 확인하세요.")
        pending.update(order_no=order_no, status="미체결")
        db.save_pending_entry(pending)
        return True

    @staticmethod
    def _exclude_unlocked(code: str, info: dict) -> bool:
        today = datetime.now(KST).date().isoformat()
        if db.is_candidate_excluded(code, today):
            return True
        upper = parse_number(info.get("upl_pric"))
        high = parse_number(info.get("high_pric"))
        current = parse_number(info.get("cur_prc"))
        if upper > 0 and high >= upper and current > 0 and (
            current < upper or kiwoom.had_unlocked_limit(code, upper)
        ):
            db.exclude_candidate(code, today, "상한가 도달 후 이탈")
            return True
        return False

    def _monitor_positions(self, settings) -> None:
        now = datetime.now(KST)
        account_positions = kiwoom.holding_positions() if db.positions() else []
        reconcile_broker_exits(db, kiwoom, account_positions, now)
        for position in db.positions():
            actual = next(
                (row for row in account_positions if str(row.get("stk_cd", "")).replace("A", "", 1) == position["stock_code"]),
                None,
            )
            if not actual or parse_number(actual.get("cur_qty")) < 1:
                continue
            if parse_number(actual.get("buy_uv")):
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
        pending = next((row for row in db.pending_entries() if row["stock_code"] == position["stock_code"]), None)
        if pending:
            # 남은 매수가 체결되며 보유량이 바뀌는 동안 매도 수량을 확정하지 않는다.
            if self.running and self.armed:
                request_cancel(db, kiwoom, pending, datetime.now(KST))
            return
        use_nxt = settings.exit_session == "NXT" or (
            settings.exit_session == "AUTO" and position.get("nxt_enabled") and reason == "NEXT_DAY_OPEN"
        )
        exchange = "NXT" if use_nxt else "KRX"
        order_no = kiwoom.sell_market(exchange=exchange, code=position["stock_code"], quantity=position["quantity"])
        db.add_pending_exit({
            "stock_code": position["stock_code"], "stock_name": position["stock_name"],
            "quantity": position["quantity"], "entry_price": position["average_price"],
            "stop_loss_rate": position["stop_loss_rate"], "level": position["level"],
            "position_rate": position["position_rate"],
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
                    position_rate=item["position_rate"],
                    stop_loss_rate=item["stop_loss_rate"], entered_at=datetime.fromisoformat(item["entered_at"]),
                    exited_at=datetime.fromisoformat(item["submitted_at"]),
                ))
                db.remove_pending_exit(item["stock_code"])


engine = TradingEngine()
