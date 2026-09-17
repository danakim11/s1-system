from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class StrategySettings(BaseModel):
    entry_rate: float = Field(29.0, ge=20, le=30)
    stop_loss_rate: float = Field(1.0, gt=0, le=10)
    min_turnover_eok: float = Field(300.0, ge=0)
    min_market_cap_eok: float = Field(1000.0, ge=0)
    trading_start: str = "08:00"
    trading_end: str = "20:00"
    exit_session: Literal["AUTO", "NXT", "KRX"] = "AUTO"
    applied_level: Literal[90, 180, 270] = 90
    max_positions: int = Field(1, ge=1, le=10)
    scan_interval_seconds: int = Field(3, ge=1, le=60)
    exchange: Literal["SOR", "KRX", "NXT"] = "SOR"

    @model_validator(mode="after")
    def validate_time_order(self):
        for value in (self.trading_start, self.trading_end):
            datetime.strptime(value, "%H:%M")
        if self.trading_start >= self.trading_end:
            raise ValueError("매매 시작시간은 종료시간보다 빨라야 합니다.")
        return self


class TradeCreate(BaseModel):
    stock_code: str = Field(min_length=6, max_length=9)
    stock_name: str = Field(min_length=1, max_length=50)
    entry_price: float = Field(gt=0)
    exit_price: float = Field(gt=0)
    quantity: int = Field(gt=0)
    exit_reason: Literal["STOP_LOSS", "NEXT_DAY_OPEN", "MANUAL"] = "MANUAL"
    level: Literal[90, 180, 270] = 90
    stop_loss_rate: float = Field(gt=0, le=10)
    entered_at: datetime
    exited_at: datetime

    @property
    def return_pct(self) -> float:
        return round((self.exit_price / self.entry_price - 1) * 100, 4)

    @property
    def realized_pnl(self) -> float:
        return round((self.exit_price - self.entry_price) * self.quantity)


class ArmRequest(BaseModel):
    phrase: str

