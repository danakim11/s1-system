from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    kiwoom_mode: str = "real"
    live_trading: bool = False
    live_confirm_phrase: str = "S1 LIVE"
    kiwoom_trading_value_unit_won: int = 1_000_000
    s1_db_path: str = "data/s1.db"

    @property
    def db_path(self) -> Path:
        return Path(self.s1_db_path)


env = Environment()

