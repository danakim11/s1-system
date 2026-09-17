import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from .config import env
from .models import StrategySettings, TradeCreate


class Database:
    def __init__(self) -> None:
        env.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = env.db_path
        self.initialize()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                  id INTEGER PRIMARY KEY CHECK(id = 1), payload TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trades (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  stock_code TEXT NOT NULL, stock_name TEXT NOT NULL,
                  entry_price REAL NOT NULL, exit_price REAL NOT NULL, quantity INTEGER NOT NULL,
                  exit_reason TEXT NOT NULL, level INTEGER NOT NULL, stop_loss_rate REAL NOT NULL,
                  position_rate REAL NOT NULL DEFAULT 90,
                  entered_at TEXT NOT NULL, exited_at TEXT NOT NULL,
                  return_pct REAL NOT NULL, realized_pnl REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS positions (
                  stock_code TEXT PRIMARY KEY, stock_name TEXT NOT NULL, exchange TEXT NOT NULL,
                  quantity INTEGER NOT NULL, average_price REAL NOT NULL, stop_loss_rate REAL NOT NULL,
                  level INTEGER NOT NULL, nxt_enabled INTEGER NOT NULL DEFAULT 0,
                  position_rate REAL NOT NULL DEFAULT 90,
                  opened_at TEXT NOT NULL, order_no TEXT
                );
                CREATE TABLE IF NOT EXISTS blocked_entries (
                  stock_code TEXT NOT NULL, trade_date TEXT NOT NULL,
                  PRIMARY KEY(stock_code, trade_date)
                );
                CREATE TABLE IF NOT EXISTS pending_exits (
                  stock_code TEXT PRIMARY KEY, stock_name TEXT NOT NULL, quantity INTEGER NOT NULL,
                  entry_price REAL NOT NULL, stop_loss_rate REAL NOT NULL, level INTEGER NOT NULL,
                  position_rate REAL NOT NULL DEFAULT 90,
                  entered_at TEXT NOT NULL, exit_reason TEXT NOT NULL, order_no TEXT,
                  submitted_at TEXT NOT NULL
                );
                """
            )
            for table in ("trades", "positions", "pending_exits"):
                if self._add_column_if_missing(conn, table, "position_rate", "REAL NOT NULL DEFAULT 90"):
                    conn.execute(f"UPDATE {table} SET position_rate=level")

    @staticmethod
    def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> bool:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            return True
        return False

    def get_settings(self) -> StrategySettings:
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM settings WHERE id=1").fetchone()
        if not row:
            settings = StrategySettings()
            self.save_settings(settings)
            return settings
        return StrategySettings.model_validate_json(row["payload"])

    def save_settings(self, settings: StrategySettings) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO settings(id,payload,updated_at) VALUES(1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                (settings.model_dump_json(), datetime.now().isoformat()),
            )

    def add_trade(self, trade: TradeCreate) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO trades(stock_code,stock_name,entry_price,exit_price,quantity,
                exit_reason,level,stop_loss_rate,position_rate,entered_at,exited_at,return_pct,realized_pnl)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trade.stock_code, trade.stock_name, trade.entry_price, trade.exit_price, trade.quantity,
                    trade.exit_reason, trade.level, trade.stop_loss_rate, trade.applied_position_rate,
                    trade.entered_at.isoformat(),
                    trade.exited_at.isoformat(), trade.return_pct, trade.realized_pnl,
                ),
            )
            return int(cur.lastrowid)

    def trades(self, limit: int = 200) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM trades ORDER BY exited_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in reversed(rows)]

    def positions(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM positions ORDER BY opened_at").fetchall()
        return [dict(row) for row in rows]

    def upsert_position(self, row: dict) -> None:
        keys = ["stock_code", "stock_name", "exchange", "quantity", "average_price", "stop_loss_rate", "level", "position_rate", "nxt_enabled", "opened_at", "order_no"]
        with self.connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO positions({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",
                tuple(row.get(key) for key in keys),
            )

    def remove_position(self, stock_code: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM positions WHERE stock_code=?", (stock_code,))

    def block_entry(self, stock_code: str, trade_date: str) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO blocked_entries VALUES(?,?)", (stock_code, trade_date))

    def is_blocked(self, stock_code: str, trade_date: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM blocked_entries WHERE stock_code=? AND trade_date=?", (stock_code, trade_date)
            ).fetchone()
        return bool(row)

    def add_pending_exit(self, row: dict) -> None:
        keys = ["stock_code", "stock_name", "quantity", "entry_price", "stop_loss_rate", "level", "position_rate", "entered_at", "exit_reason", "order_no", "submitted_at"]
        with self.connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO pending_exits({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",
                tuple(row.get(key) for key in keys),
            )

    def pending_exits(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM pending_exits ORDER BY submitted_at").fetchall()
        return [dict(row) for row in rows]

    def remove_pending_exit(self, stock_code: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM pending_exits WHERE stock_code=?", (stock_code,))


db = Database()
