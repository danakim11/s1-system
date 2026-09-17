"""앱에서 추적하는 보유분의 키움 외부 매도를 체결 내역으로 기록한다."""
from datetime import datetime, timedelta

from .models import TradeCreate
from .strategy import parse_number


def reconcile_broker_exits(db, broker, account_positions: list[dict], now: datetime) -> None:
    actual = {str(row.get("stk_cd", "")).removeprefix("A"): int(parse_number(row.get("cur_qty")))
              for row in account_positions}
    pending_codes = {row["stock_code"] for row in db.pending_entries()}
    cache = {}
    for position in db.positions():
        if position["stock_code"] in pending_codes:
            continue
        missing = position["quantity"] - actual.get(position["stock_code"], 0)
        if missing <= 0:
            continue
        entered = datetime.fromisoformat(position["opened_at"])
        day = entered.date()
        while day <= now.date() and missing > 0:
            if day not in cache:
                cache[day] = broker.sell_order_details(day.strftime("%Y%m%d"))
            for row in sorted(cache[day], key=lambda r: str(r.get("ord_tm", ""))):
                if str(row.get("stk_cd", "")).removeprefix("A") != position["stock_code"]:
                    continue
                number = str(row.get("ord_no", ""))
                total_qty = int(parse_number(row.get("cntr_qty")))
                total_price = parse_number(row.get("cntr_uv"))
                clock = str(row.get("ord_tm", "")).replace(":", "")
                if not number or total_qty < 1 or total_price <= 0 or len(clock) != 6 or not clock.isdigit():
                    continue
                exited = datetime.combine(day, datetime.strptime(clock, "%H%M%S").time(), tzinfo=now.tzinfo)
                if exited < entered.replace(microsecond=0):
                    continue
                imported_qty, imported_amount = db.imported_broker_exit(day.isoformat(), number)
                delta = total_qty - imported_qty
                if delta <= 0:
                    continue
                price = (total_qty * total_price - imported_amount) / delta
                if price <= 0:
                    continue
                quantity = min(missing, delta)
                trade = TradeCreate(
                    stock_code=position["stock_code"], stock_name=position["stock_name"],
                    entry_price=position["average_price"], exit_price=price, quantity=quantity,
                    exit_reason="BROKER_EXIT", level=position["level"], position_rate=position["position_rate"],
                    stop_loss_rate=position["stop_loss_rate"], entered_at=entered, exited_at=exited,
                )
                if db.record_broker_exit(trade, number):
                    missing -= quantity
                if missing == 0:
                    break
            day += timedelta(days=1)
