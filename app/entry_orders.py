"""매수 접수와 실제 체결을 구분하고 미체결 잔량은 즉시 취소한다."""
from datetime import datetime

from .strategy import parse_number


def order_key(value) -> str:
    return str(value or "").lstrip("0")


def at_upper_limit(info: dict) -> bool:
    upper = parse_number(info.get("upl_pric"))
    current = parse_number(info.get("cur_prc"))
    return upper > 0 and current >= upper


def request_cancel(db, broker, pending: dict, now: datetime) -> None:
    if pending.get("cancel_requested_at") or not order_key(pending.get("order_no")):
        return
    # 통신 오류 시 중복 취소를 보내지 않고 증권사 내역으로 확인한다.
    pending["cancel_requested_at"] = now.isoformat()
    pending["status"] = "취소 확인 대기"
    db.save_pending_entry(pending)
    pending["cancel_order_no"] = broker.cancel_buy(
        exchange=pending["exchange"], code=pending["stock_code"], order_no=pending["order_no"],
    )
    db.save_pending_entry(pending)


def reconcile_entries(db, broker, now: datetime, *, allow_cancel: bool) -> None:
    legacy_positions = [row for row in db.positions() if not row.get("entry_confirmed")]
    tracked = legacy_positions + db.pending_entries()
    if not tracked:
        return
    unfilled = {order_key(row.get("ord_no")): row for row in broker.unfilled_buys()}
    details = {}
    for day in sorted({row["opened_at"][:10].replace("-", "") for row in tracked}):
        for row in broker.buy_order_details(day):
            details[(day, order_key(row.get("ord_no")))] = row

    def detail_for(row):
        return details.get((row["opened_at"][:10].replace("-", ""), order_key(row.get("order_no"))))

    def open_for(row):
        if row["opened_at"][:10] != now.date().isoformat():
            return None
        order = unfilled.get(order_key(row.get("order_no")))
        if order and str(order.get("stk_cd", "")).removeprefix("A") == row["stock_code"]:
            return order
        return None

    # 이전 버전은 주문 접수만으로 보유 행을 만들었다. 증권사 확인 후 이동한다.
    pending_codes = {row["stock_code"] for row in db.pending_entries()}
    for position in legacy_positions:
        if position["stock_code"] in pending_codes:
            continue
        detail = detail_for(position)
        open_order = open_for(position)
        if open_order or (detail and parse_number(detail.get("cntr_qty")) == 0):
            pending = {**position, "status": "체결 확인 대기", "filled_quantity": 0}
            db.save_pending_entry(pending, remove_legacy_position=True)
        elif detail and parse_number(detail.get("cntr_qty")) > 0:
            db.upsert_position({**position, "entry_confirmed": 1})

    for pending in db.pending_entries():
        detail = detail_for(pending)
        open_order = open_for(pending)
        if not detail or detail.get("cntr_qty") in (None, "") or detail.get("ord_remnq") in (None, ""):
            # 누락된 조회를 취소 완료로 간주하지 않는다.
            continue
        filled = int(parse_number(detail["cntr_qty"]))
        remaining = int(parse_number(detail["ord_remnq"]))
        price = parse_number(detail.get("cntr_uv"))
        if filled and not price:
            continue
        if filled:
            db.upsert_position({**pending, "quantity": filled, "average_price": price, "entry_confirmed": 1})
        pending["filled_quantity"] = filled
        pending["remaining_quantity"] = remaining
        # 미체결 목록과 원주문 잔량 두 조회가 모두 완료를 가리킬 때만 자리를 해제한다.
        if remaining == 0 and not open_order:
            db.remove_pending_entry(pending["stock_code"])
            continue
        pending["status"] = "취소 확인 대기" if pending.get("cancel_requested_at") else ("부분 체결" if filled else "미체결")
        if remaining > 0 and open_order and not pending.get("cancel_requested_at"):
            db.save_pending_entry(pending)
            if allow_cancel:
                request_cancel(db, broker, pending, now)
        else:
            db.save_pending_entry(pending)
