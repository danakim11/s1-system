import pytest
from pydantic import ValidationError

from app.kiwoom_service import KiwoomService, KiwoomUnavailable
from app.models import StrategySettings
from app.strategy import position_budget


def test_unsettled_cash_does_not_make_investment_budget_zero(monkeypatch):
    service = KiwoomService()
    responses = {
        "kt00005": {
            "entr": "000000000000", "evlt_amt_tot": "000000000000",
            "20ord_alow_amt": "000010000000", "stk_cntr_remn": [],
        },
        "kt00003": {"prsm_dpst_aset_amt": "000002000000"},
    }
    monkeypatch.setattr(service, "fetch", lambda api_id, path, body: responses[api_id])
    account = service.account()
    assert account.equity == 2_000_000
    assert account.available_20 == 10_000_000
    assert position_budget(account.equity, 10, 2, account.available_20) == pytest.approx(100_000)
    assert position_budget(account.equity, 270, 2, account.available_20) == pytest.approx(2_700_000)


@pytest.mark.parametrize("value", ["-100", "0", "000000000000"])
def test_nonpositive_balance_cannot_fund_orders(value):
    assert KiwoomService._account_amount({"amount": value}, "amount") == 0


@pytest.mark.parametrize("payload", [{}, {"amount": ""}, {"amount": None}, {"amount": "NaN"}, {"amount": "Infinity"}])
def test_missing_or_invalid_balance_is_a_lookup_error(payload):
    with pytest.raises(KiwoomUnavailable, match="amount"):
        KiwoomService._account_amount(payload, "amount")


@pytest.mark.parametrize("level", [10, 90, 180, 270])
def test_supported_modes(level):
    assert StrategySettings(applied_level=level).applied_level == level


def test_removed_twenty_percent_mode_is_rejected():
    with pytest.raises(ValidationError):
        StrategySettings(applied_level=20)
