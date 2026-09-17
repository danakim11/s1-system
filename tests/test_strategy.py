from app.strategy import candidate_allowed, position_budget, recommend_level, summarize
from app.models import StrategySettings


def rows(values):
    return [{"return_pct": value, "realized_pnl": value * 10_000} for value in values]


def test_fast_down_on_three_losses():
    assert recommend_level(rows([3, 4, -1, -1, -1]))[0] == 90


def test_slow_up_requires_ten_trades():
    assert recommend_level(rows([3, 3, 3, 3, 3]))[0] == 180
    assert recommend_level(rows([3, 3, -1, 3, 3, -1, 3, 3, -1, 3]))[0] == 270


def test_position_budget_is_capped_by_available_amount():
    assert position_budget(100_000_000, 270, 1, 200_000_000) == 200_000_000


def test_candidate_filters_all_three_numbers():
    settings = StrategySettings()
    assert candidate_allowed(change_rate=29, turnover_won=30_000_000_000, market_cap_eok=1000, settings=settings)
    assert not candidate_allowed(change_rate=28.9, turnover_won=30_000_000_000, market_cap_eok=1000, settings=settings)


def test_summary():
    result = summarize(rows([4, -1, 2, -2]))
    assert result.win_rate == 50
    assert result.expectancy == 0.75

