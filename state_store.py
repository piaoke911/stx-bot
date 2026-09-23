# ===================== state_store.py =====================
"""
state_store.py
==============
基于 SQLite 的状态持久化层。

解决的问题（P0 级）：
------------------------------------------------------------------
原版本所有状态都在内存中，重启即清零：
1. Paper 账户余额/持仓 → 重启后从初始资金重新开始，之前所有模拟盈亏丢失。
2. 熔断器状态 → 重启后熔断被绕过，可能继续亏损。
3. 交易历史 → 无法长期统计策略表现。
4. 连续亏损计数 → 重启即清零。

本模块提供：
- key-value 存储（存 JSON 快照）
- trades 表（交易历史查询）
- 线程安全（SQLite 单文件 + 短事务）
------------------------------------------------------------------
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger("stx_quant.state_store")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv_store (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    side        TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price  REAL NOT NULL,
    size        REAL NOT NULL,
    pnl         REAL NOT NULL,
    opened_at   TEXT NOT NULL,
    closed_at   TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_trades_closed_at ON trades(closed_at);
"""


class StateStore:
    """
    状态持久化。

    使用方式：
        store = StateStore("data/stx_bot.db")
        store.set_json("paper_account", {"balance": 10000, ...})
        snap = store.get_json("paper_account")

        store.append_trade({...})
        trades = store.list_trades(limit=100)
    """

    def __init__(self, db_path: str = "data/stx_bot.db") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        logger.info("StateStore 初始化完成 | 路径=%s", db_path)

    # ---------------------- 内部连接 ----------------------

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """每次操作打开一个短连接，避免多线程共享。"""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ---------------------- KV 接口 ----------------------

    def set_json(self, key: str, value: Any) -> None:
        """写入任意 JSON 可序列化的值。"""
        payload = json.dumps(value, ensure_ascii=False, default=str)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO kv_store(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, payload, now),
            )

    def get_json(self, key: str) -> Optional[Any]:
        """读取 JSON 值，不存在则返回 None。"""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM kv_store WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError as exc:
            logger.error("反序列化 key=%s 失败: %s", key, exc)
            return None

    def delete(self, key: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM kv_store WHERE key = ?", (key,))

    # ---------------------- 交易记录接口 ----------------------

    def append_trade(self, trade: dict) -> None:
        """
        trade 需包含字段：
            side, entry_price, exit_price, size, pnl, opened_at, closed_at
        opened_at / closed_at 可以是 datetime 或 isoformat 字符串。
        """
        opened_at = trade.get("opened_at")
        closed_at = trade.get("closed_at")
        if isinstance(opened_at, datetime):
            opened_at = opened_at.isoformat()
        if isinstance(closed_at, datetime):
            closed_at = closed_at.isoformat()
        if closed_at is None:
            closed_at = datetime.now(timezone.utc).isoformat()

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trades(side, entry_price, exit_price, size, pnl, opened_at, closed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(trade["side"]),
                    float(trade["entry_price"]),
                    float(trade["exit_price"]),
                    float(trade["size"]),
                    float(trade["pnl"]),
                    str(opened_at),
                    str(closed_at),
                ),
            )

    def list_trades(self, limit: int = 100) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def aggregate_stats(self) -> dict:
        """聚合统计：总交易数、胜率、总盈亏、平均盈亏。"""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                    COALESCE(SUM(pnl), 0.0) AS total_pnl
                FROM trades
                """
            ).fetchone()
        total = int(row["total"] or 0)
        wins = int(row["wins"] or 0)
        total_pnl = float(row["total_pnl"] or 0.0)
        return {
            "total_trades": total,
            "win_rate": (wins / total) if total > 0 else 0.0,
            "total_pnl": total_pnl,
            "avg_pnl": (total_pnl / total) if total > 0 else 0.0,
        }

    def close(self) -> None:
        """当前实现无需显式关闭（每次操作短连接），保留接口方便未来扩展。"""
        pass
