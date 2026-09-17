from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import time
from typing import Any

from .config import env
from .strategy import parse_number

class KiwoomUnavailable(RuntimeError):
    pass


@dataclass
class AccountSnapshot:
    equity: float
    available_20: float
    positions: list[dict]


class KiwoomService:
    """공식 kwcli 패키지의 인증·REST 클라이언트를 얇게 감싼다."""

    def __init__(self):
        self._limit_history_cache = {}

    @staticmethod
    def _client():
        try:
            # pydantic-settings는 .env를 읽지만 외부 라이브러리의 환경에는
            # 자동 전파하지 않는다. 키움 공식 변수명으로 명시적으로 전달한다.
            os.environ.setdefault("KIWOOM_MODE", env.kiwoom_mode)
            if env.app_key:
                os.environ.setdefault("APP_KEY", env.app_key)
            if env.app_secret:
                os.environ.setdefault("APP_SECRET", env.app_secret)
            from kiwoom import get_client

            return get_client()
        except Exception as exc:
            raise KiwoomUnavailable(
                "키움 인증에 실패했습니다. .env의 운영용 APP_KEY/APP_SECRET을 확인하거나 "
                "`kiwoomcli setup`으로 운영 프로필을 연결하세요."
            ) from exc

    def fetch(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._client().fetch_page(api_id=api_id, path=path, body=body)
        payload = response.body
        if payload.get("return_code") not in (None, 0):
            raise KiwoomUnavailable(payload.get("return_msg") or "키움 API 요청에 실패했습니다.")
        return payload

    def trading_value_top(self, *, on_page=None) -> list[dict]:
        return self.candidate_display_rows(on_page=on_page)

    def stock_info(self, code: str) -> dict:
        return self.fetch("ka10001", "/api/dostk/stkinfo", {"stk_cd": code})

    def near_entry_rows(self, min_rate: float, *, on_page=None) -> list[dict]:
        """전 시장을 상승률순으로 조회. 순위 개수 제한 없이 기준 이상을 모두 수집한다."""
        rows = {}
        for response in self._client().iterate_pages(
            api_id="ka10027", path="/api/dostk/rkinfo", max_pages=0,
            body={"mrkt_tp": "000", "sort_tp": "1", "trde_qty_cnd": "0000", "stk_cnd": "0",
                  "crd_cnd": "0", "updown_incls": "1", "pric_cnd": "0", "trde_prica_cnd": "0", "stex_tp": "3"},
        ):
            if response.body.get("return_code") not in (None, 0):
                raise KiwoomUnavailable(response.body.get("return_msg") or "등락률 조회 실패")
            page = response.body.get("pred_pre_flu_rt_upper", [])
            for row in page:
                if parse_number(row.get("flu_rt"), absolute=False) >= min_rate:
                    rows[str(row.get("stk_cd", ""))] = row
            if on_page is not None:
                on_page()
            # 상승률 내림차순이므로 기준 미만에 도달하면 이후 순위는 후보가 아니다.
            if page and parse_number(page[-1].get("flu_rt"), absolute=False) < min_rate:
                break
        return list(rows.values())

    def today_turnover(self, code: str) -> float:
        day = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
        payload = self.fetch("ka10081", "/api/dostk/chart", {
            "stk_cd": code + "_AL", "base_dt": day, "upd_stkpc_tp": "1",
        })
        row = next((r for r in payload.get("stk_dt_pole_chart_qry", []) if r.get("dt") == day), None)
        if row is None or row.get("trde_prica") in (None, ""):
            raise KiwoomUnavailable(f"{code} 당일 거래대금을 확인하지 못했습니다.")
        return parse_number(row["trde_prica"]) * 1_000_000

    def had_unlocked_limit(self, code: str, upper: float) -> bool:
        day = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
        key = (day, code, upper)
        cached = self._limit_history_cache.get(key)
        if cached and (cached[1] or time.monotonic() - cached[0] < 30):
            return cached[1]
        bars = {}
        for response in self._client().iterate_pages(
            api_id="ka10080", path="/api/dostk/chart", max_pages=0,
            body={"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1", "base_dt": day},
        ):
            if response.body.get("return_code") not in (None, 0):
                raise KiwoomUnavailable(response.body.get("return_msg") or "상한가 이력 조회 실패")
            page = response.body.get("stk_min_pole_chart_qry", [])
            for bar in page:
                stamp = str(bar.get("cntr_tm", ""))
                if stamp.startswith(day):
                    bars[stamp] = bar
            if any(str(bar.get("cntr_tm", ""))[:8] < day for bar in page):
                break
        reached = False
        unlocked = False
        for _, bar in sorted(bars.items()):
            low = parse_number(bar.get("low_pric"))
            high = parse_number(bar.get("high_pric"))
            close = parse_number(bar.get("cur_prc"))
            if (reached and 0 < low < upper) or (high >= upper and 0 < close < upper):
                unlocked = True
                break
            reached = reached or high >= upper
        self._limit_history_cache[key] = (time.monotonic(), unlocked)
        return unlocked

    def candidate_display_rows(self, *, on_page=None) -> list[dict]:
        """키움 거래대금 순위의 모든 페이지를 조회한다."""
        rows: dict[str, dict] = {}
        for response in self._client().iterate_pages(
            api_id="ka10032", path="/api/dostk/rkinfo",
            body={"mrkt_tp": "000", "mang_stk_incls": "0", "stex_tp": "3"},
            max_pages=0,
        ):
            payload = response.body
            if payload.get("return_code") not in (None, 0):
                raise KiwoomUnavailable(payload.get("return_msg") or "후보 연속조회에 실패했습니다.")
            for row in payload.get("trde_prica_upper", []):
                rows[str(row.get("stk_cd", ""))] = row
            if on_page is not None:
                on_page()
        return list(rows.values())

    def stock_detail(self, code: str) -> dict:
        return self.fetch("ka10100", "/api/dostk/stkinfo", {"stk_cd": code})

    def account(self, exchange: str = "KRX") -> AccountSnapshot:
        payload = self.fetch("kt00005", "/api/dostk/acnt", {"dmst_stex_tp": exchange})
        # 당일 예수금은 매도대금 결제 전에는 0일 수 있다.
        # 주문 한도와 투자 기준 자산을 구분해 증권사의 추정예탁자산을 사용한다.
        assets = self.fetch("kt00003", "/api/dostk/acnt", {"qry_tp": "0"})
        return AccountSnapshot(
            equity=self._account_amount(assets, "prsm_dpst_aset_amt"),
            available_20=self._account_amount(payload, "20ord_alow_amt"),
            positions=payload.get("stk_cntr_remn", []),
        )

    @staticmethod
    def _account_amount(payload: dict, field: str) -> float:
        try:
            amount = Decimal(str(payload[field]).strip().replace(",", ""))
            if not amount.is_finite():
                raise ValueError
        except (KeyError, InvalidOperation, ValueError):
            raise KiwoomUnavailable(f"계좌 금액 조회값을 확인할 수 없습니다: {field}") from None
        # 음수 잔액을 절댓값으로 바꿔 주문 여력으로 취급하지 않는다.
        return float(max(Decimal(0), amount))

    def _account_rows(self, api_id: str, body: dict, field: str) -> list[dict]:
        rows = []
        for response in self._client().iterate_pages(
            api_id=api_id, path="/api/dostk/acnt", body=body, max_pages=0,
        ):
            if response.body.get("return_code") not in (None, 0):
                raise KiwoomUnavailable(response.body.get("return_msg") or "주문 내역 조회 실패")
            rows.extend(response.body.get(field, []))
        return rows

    def unfilled_buys(self) -> list[dict]:
        return self._account_rows("ka10075", {
            "all_stk_tp": "0", "trde_tp": "2", "stk_cd": "", "stex_tp": "0",
        }, "oso")

    def holding_positions(self) -> list[dict]:
        return self._account_rows("kt00005", {"dmst_stex_tp": "KRX"}, "stk_cntr_remn")

    def buy_order_details(self, order_date: str) -> list[dict]:
        return self._account_rows("kt00007", {
            "ord_dt": order_date, "qry_tp": "1", "stk_bond_tp": "1", "sell_tp": "2",
            "stk_cd": "", "fr_ord_no": "", "dmst_stex_tp": "%",
        }, "acnt_ord_cntr_prps_dtl")

    def sell_order_details(self, order_date: str) -> list[dict]:
        return self._account_rows("kt00007", {
            "ord_dt": order_date, "qry_tp": "1", "stk_bond_tp": "1", "sell_tp": "1",
            "stk_cd": "", "fr_ord_no": "", "dmst_stex_tp": "%",
        }, "acnt_ord_cntr_prps_dtl")

    def cancel_buy(self, *, exchange: str, code: str, order_no: str) -> str:
        payload = self.fetch("kt10003", "/api/dostk/ordr", {
            "dmst_stex_tp": exchange, "orig_ord_no": order_no, "stk_cd": code, "cncl_qty": "0",
        })
        number = str(payload.get("ord_no", ""))
        if not number:
            raise KiwoomUnavailable("취소 주문번호를 확인하지 못했습니다. 주문 내역 확인이 필요합니다.")
        return number

    def buy_market(self, *, exchange: str, code: str, quantity: int) -> str:
        payload = self.fetch(
            "kt10000", "/api/dostk/ordr",
            {"dmst_stex_tp": exchange, "stk_cd": code, "ord_qty": str(quantity), "trde_tp": "13", "ord_uv": "", "cond_uv": ""},
        )
        return str(payload.get("ord_no", ""))

    def sell_market(self, *, exchange: str, code: str, quantity: int) -> str:
        payload = self.fetch(
            "kt10001", "/api/dostk/ordr",
            {"dmst_stex_tp": exchange, "stk_cd": code, "ord_qty": str(quantity), "trde_tp": "3", "ord_uv": "", "cond_uv": ""},
        )
        return str(payload.get("ord_no", ""))

    def today_trade_journal(self, base_date: str) -> list[dict]:
        payload = self.fetch(
            "ka10170", "/api/dostk/acnt",
            {"ottks_tp": "2", "ch_crd_tp": "0", "base_dt": base_date},
        )
        return payload.get("tdy_trde_diary", [])


kiwoom = KiwoomService()
