from dataclasses import dataclass
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

    @staticmethod
    def _client():
        try:
            from kiwoom import get_client

            return get_client()
        except Exception as exc:
            raise KiwoomUnavailable(
                "키움 인증이 없습니다. 먼저 `kiwoomcli setup`으로 운영 프로필을 연결하세요."
            ) from exc

    def fetch(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._client().fetch_page(api_id=api_id, path=path, body=body)
        payload = response.body
        if payload.get("return_code") not in (None, 0):
            raise KiwoomUnavailable(payload.get("return_msg") or "키움 API 요청에 실패했습니다.")
        return payload

    def trading_value_top(self) -> list[dict]:
        payload = self.fetch(
            "ka10032", "/api/dostk/rkinfo",
            {"mrkt_tp": "000", "mang_stk_incls": "0", "stex_tp": "3"},
        )
        return payload.get("trde_prica_upper", [])

    def stock_info(self, code: str) -> dict:
        return self.fetch("ka10001", "/api/dostk/stkinfo", {"stk_cd": code})

    def stock_detail(self, code: str) -> dict:
        return self.fetch("ka10100", "/api/dostk/stkinfo", {"stk_cd": code})

    def account(self, exchange: str = "KRX") -> AccountSnapshot:
        payload = self.fetch("kt00005", "/api/dostk/acnt", {"dmst_stex_tp": exchange})
        equity = parse_number(payload.get("evlt_amt_tot")) + parse_number(payload.get("entr"))
        return AccountSnapshot(
            equity=equity,
            available_20=parse_number(payload.get("20ord_alow_amt")),
            positions=payload.get("stk_cntr_remn", []),
        )

    def buy_market(self, *, exchange: str, code: str, quantity: int) -> str:
        payload = self.fetch(
            "kt10000", "/api/dostk/ordr",
            {"dmst_stex_tp": exchange, "stk_cd": code, "ord_qty": str(quantity), "trde_tp": "3", "ord_uv": "", "cond_uv": ""},
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
