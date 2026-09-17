from dataclasses import dataclass
from datetime import datetime, time
from statistics import fmean
from zoneinfo import ZoneInfo

from .models import StrategySettings

KST = ZoneInfo("Asia/Seoul")


def parse_number(value: object) -> float:
    if value is None:
        return 0.0
    text = str(value).strip().replace(",", "").replace("+", "")
    if not text:
        return 0.0
    try:
        return abs(float(text))
    except (TypeError, ValueError):
        return 0.0


def consecutive_losses(returns: list[float]) -> int:
    streak = 0
    for value in reversed(returns):
        if value < 0:
            streak += 1
        else:
            break
    return streak


@dataclass(frozen=True)
class Performance:
    count: int
    wins: int
    losses: int
    win_rate: float
    expectancy: float
    average_win: float
    average_loss: float
    cumulative_pnl: float
    loss_streak: int


def summarize(rows: list[dict]) -> Performance:
    returns = [float(row.get("return_pct", 0)) for row in rows]
    wins = [v for v in returns if v > 0]
    losses = [v for v in returns if v < 0]
    return Performance(
        count=len(rows),
        wins=len(wins),
        losses=len(losses),
        win_rate=round(len(wins) / len(rows) * 100, 1) if rows else 0,
        expectancy=round(fmean(returns), 2) if returns else 0,
        average_win=round(fmean(wins), 2) if wins else 0,
        average_loss=round(fmean(losses), 2) if losses else 0,
        cumulative_pnl=round(sum(float(row.get("realized_pnl", 0)) for row in rows)),
        loss_streak=consecutive_losses(returns),
    )


def recommend_level(rows: list[dict]) -> tuple[int, str]:
    """빠르게 낮추고, 충분한 표본이 쌓였을 때만 천천히 높인다."""
    recent = rows[-10:]
    perf = summarize(recent)
    if perf.loss_streak >= 3 or (perf.count >= 5 and perf.expectancy <= -1.0):
        return 90, "연속 3패 또는 최근 기대값 -1% 이하"
    if perf.loss_streak >= 2 or (perf.count >= 5 and perf.expectancy <= 0):
        return 180, "연속 2패 또는 최근 기대값 0% 이하"
    if perf.count >= 10 and perf.win_rate >= 60 and perf.expectancy > 0:
        return 270, "최근 10회 승률 60% 이상·기대값 양수"
    return 180, "표본 축적 중: 기본 중립 단계"


def candidate_allowed(
    *, change_rate: float, turnover_won: float, market_cap_eok: float, settings: StrategySettings
) -> bool:
    return (
        change_rate >= settings.entry_rate
        and turnover_won >= settings.min_turnover_eok * 100_000_000
        and market_cap_eok >= settings.min_market_cap_eok
    )


def in_trading_window(settings: StrategySettings, now: datetime | None = None) -> bool:
    now = now or datetime.now(KST)
    current = now.astimezone(KST).time().replace(second=0, microsecond=0)
    start = time.fromisoformat(settings.trading_start)
    end = time.fromisoformat(settings.trading_end)
    return now.weekday() < 5 and start <= current <= end


def position_budget(equity: float, applied_level: int, max_positions: int, available: float) -> float:
    target = equity * applied_level / 100 / max_positions
    return max(0, min(target, available))

