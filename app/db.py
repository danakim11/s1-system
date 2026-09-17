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
                CREATE TABLE IF NOT EXISTS pending_entries (
                  stock_code TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidate_exclusions (
                  trade_date TEXT NOT NULL, stock_code TEXT NOT NULL,
                  reason TEXT NOT NULL, detected_at TEXT NOT NULL,
                  PRIMARY KEY(trade_date,stock_code)
                );
                CREATE TABLE IF NOT EXISTS broker_exit_sync (
                  trade_date TEXT NOT NULL, order_no TEXT NOT NULL,
                  quantity INTEGER NOT NULL, amount REAL NOT NULL,
                  PRIMARY KEY(trade_date,order_no)
                );
                CREATE TABLE IF NOT EXISTS candidate_history (
                  trade_date TEXT NOT NULL, stock_code TEXT NOT NULL,
                  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                  snapshot TEXT NOT NULL, settings TEXT NOT NULL,
                  PRIMARY KEY(trade_date, stock_code)
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
            self._add_column_if_missing(conn, "positions", "entry_confirmed", "INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> bool:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            return True
        return False

    def save_candidates(self, candidates: list[dict], settings: StrategySettings, observed_at: datetime) -> None:
        from zoneinfo import ZoneInfo
        observed_at = observed_at.astimezone(ZoneInfo("Asia/Seoul"))
        timestamp = observed_at.isoformat()
        with self.connect() as conn:
            for candidate in candidates:
                if not candidate["allowed"]:
                    continue
                conn.execute(
                    "INSERT INTO candidate_history VALUES(?,?,?,?,?,?) "
                    "ON CONFLICT(trade_date,stock_code) DO UPDATE SET "
                    "first_seen=MIN(candidate_history.first_seen,excluded.first_seen), "
                    "last_seen=MAX(candidate_history.last_seen,excluded.last_seen), "
                    "snapshot=CASE WHEN excluded.last_seen>=candidate_history.last_seen THEN excluded.snapshot ELSE candidate_history.snapshot END, "
                    "settings=CASE WHEN excluded.last_seen>=candidate_history.last_seen THEN excluded.settings ELSE candidate_history.settings END",
                    (observed_at.date().isoformat(), candidate["stock_code"], timestamp, timestamp,
                     json.dumps(candidate, ensure_ascii=False), settings.model_dump_json()),
                )

    def candidate_history(self, trade_date: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM candidate_history WHERE trade_date=? ORDER BY first_seen,stock_code", (trade_date,)
            ).fetchall()
        return [{**json.loads(row["snapshot"]), "first_seen": row["first_seen"],
                 "last_seen": row["last_seen"], "settings": json.loads(row["settings"])} for row in rows]

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

    def imported_broker_exit(self, day: str, order_no: str) -> tuple[int, float]:
        with self.connect() as conn:
            row = conn.execute("SELECT quantity,amount FROM broker_exit_sync WHERE trade_date=? AND order_no=?",
                               (day, order_no)).fetchone()
        return (row["quantity"], row["amount"]) if row else (0, 0.0)

    def record_broker_exit(self, trade: TradeCreate, order_no: str) -> bool:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            position = conn.execute("SELECT quantity FROM positions WHERE stock_code=?", (trade.stock_code,)).fetchone()
            if not position or position["quantity"] < trade.quantity:
                return False
            conn.execute(
                "INSERT INTO trades(stock_code,stock_name,entry_price,exit_price,quantity,exit_reason,level,"
                "stop_loss_rate,position_rate,entered_at,exited_at,return_pct,realized_pnl) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (trade.stock_code, trade.stock_name, trade.entry_price, trade.exit_price, trade.quantity,
                 trade.exit_reason, trade.level, trade.stop_loss_rate, trade.applied_position_rate,
                 trade.entered_at.isoformat(), trade.exited_at.isoformat(), trade.return_pct, trade.realized_pnl),
            )
            conn.execute(
                "INSERT INTO broker_exit_sync VALUES(?,?,?,?) ON CONFLICT(trade_date,order_no) DO UPDATE SET "
                "quantity=quantity+excluded.quantity,amount=amount+excluded.amount",
                (trade.exited_at.date().isoformat(), order_no, trade.quantity, trade.quantity * trade.exit_price),
            )
            conn.execute("UPDATE positions SET quantity=quantity-? WHERE stock_code=?", (trade.quantity, trade.stock_code))
            conn.execute("DELETE FROM positions WHERE stock_code=? AND quantity=0", (trade.stock_code,))
            return True

    def trades(self, limit: int = 200) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM trades ORDER BY exited_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in reversed(rows)]

    def equity_curve(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, exited_at, stock_name, realized_pnl FROM trades ORDER BY exited_at, id"
            ).fetchall()
        total = 0.0
        points = []
        for row in rows:
            total += row["realized_pnl"]
            points.append({**dict(row), "cumulative_pnl": round(total, 2)})
        return points

    def exclude_candidate(self, stock_code: str, trade_date: str, reason: str) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO candidate_exclusions VALUES(?,?,?,?)",
                         (trade_date, stock_code, reason, datetime.now().isoformat()))

    def is_candidate_excluded(self, stock_code: str, trade_date: str) -> bool:
        with self.connect() as conn:
            return conn.execute("SELECT 1 FROM candidate_exclusions WHERE trade_date=? AND stock_code=?",
                                (trade_date, stock_code)).fetchone() is not None

    def pending_entries(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT payload FROM pending_entries ORDER BY stock_code").fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def save_pending_entry(self, row: dict, *, remove_legacy_position: bool = False) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO pending_entries VALUES(?,?)",
                         (row["stock_code"], json.dumps(row, ensure_ascii=False)))
            if remove_legacy_position:
                conn.execute("DELETE FROM positions WHERE stock_code=?", (row["stock_code"],))

    def remove_pending_entry(self, code: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM pending_entries WHERE stock_code=?", (code,))

    def positions(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM positions ORDER BY opened_at").fetchall()
        return [dict(row) for row in rows]

    def upsert_position(self, row: dict) -> None:
        keys = ["stock_code", "stock_name", "exchange", "quantity", "average_price", "stop_loss_rate", "level", "position_rate", "nxt_enabled", "opened_at", "order_no", "entry_confirmed"]
        with self.connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO positions({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",
                tuple(row.get(key, 0) if key == "entry_confirmed" else row.get(key) for key in keys),
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
