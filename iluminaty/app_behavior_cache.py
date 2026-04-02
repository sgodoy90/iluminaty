"""
ILUMINATY - App Behavior Cache (Phase C)
========================================
Persistent lightweight memory of action outcomes by app/window/action.

Purpose:
- Improve first-try success on known apps.
- Reuse recovery patterns across sessions.
- Keep storage compact (SQLite metadata only, no frames/images).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional


def _norm(value: Optional[str], fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    return text if text else fallback


class AppBehaviorCache:
    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self._db_path = Path(db_path)
        else:
            self._db_path = Path.home() / ".iluminaty" / "app_behavior_cache.sqlite3"
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._available = False
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._init_schema()
            self._available = True
        except Exception as exc:
            import logging as _log
            _log.getLogger(__name__).warning(
                "AppBehaviorCache unavailable (DB open failed: %s) — cache disabled, "
                "ILUMINATY will continue without persistent behavior memory.", exc
            )

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_ms INTEGER NOT NULL,
                    app_name TEXT NOT NULL,
                    window_title TEXT NOT NULL,
                    action TEXT NOT NULL,
                    params_sig TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    method_used TEXT NOT NULL,
                    recovery_used INTEGER NOT NULL,
                    recovery_strategy TEXT NOT NULL,
                    duration_ms REAL NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_outcomes_app_action_ts ON outcomes(app_name, action, ts_ms DESC)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_outcomes_window_action_ts ON outcomes(window_title, action, ts_ms DESC)"
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def _params_signature(self, params: dict) -> str:
        params = dict(params or {})
        compact = {}
        for key in sorted(params.keys()):
            if key.startswith("_"):
                continue
            value = params[key]
            if isinstance(value, (str, int, float, bool)) or value is None:
                compact[key] = value
            else:
                compact[key] = str(type(value).__name__)
        try:
            return json.dumps(compact, separators=(",", ":"), sort_keys=True)[:360]
        except Exception:
            return "{}"

    def record_outcome(
        self,
        *,
        app_name: str,
        window_title: str,
        action: str,
        params: Optional[dict],
        success: bool,
        reason: str,
        method_used: str = "",
        recovery_used: bool = False,
        recovery_strategy: str = "",
        duration_ms: float = 0.0,
    ) -> None:
        if not self._available or self._conn is None:
            return
        row = (
            int(time.time() * 1000),
            _norm(app_name),
            _norm(window_title),
            _norm(action),
            self._params_signature(params or {}),
            1 if bool(success) else 0,
            _norm(reason, fallback=""),
            _norm(method_used, fallback=""),
            1 if bool(recovery_used) else 0,
            _norm(recovery_strategy, fallback=""),
            float(duration_ms or 0.0),
        )
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO outcomes(
                        ts_ms, app_name, window_title, action, params_sig, success,
                        reason, method_used, recovery_used, recovery_strategy, duration_ms
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    row,
                )
                self._conn.commit()
            except Exception as exc:
                import logging as _log
                _log.getLogger(__name__).debug("AppBehaviorCache record_outcome failed: %s", exc)

    def suggest(
        self,
        *,
        action: str,
        app_name: str,
        window_title: str,
        lookback: int = 60,
    ) -> dict:
        if not self._available or self._conn is None:
            return {"found": False, "reason": "cache_unavailable", "sample_size": 0,
                    "recommended_retries": 1, "recommended_pre_delay_ms": 0,
                    "focus_before_action": False, "preferred_method": None,
                    "action": _norm(action), "app_name": _norm(app_name), "window_title": _norm(window_title)}
        action_norm = _norm(action)
        app_norm = _norm(app_name)
        title_norm = _norm(window_title)

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT success, reason, recovery_used, recovery_strategy, duration_ms, method_used
                FROM outcomes
                WHERE action = ? AND (app_name = ? OR window_title = ?)
                ORDER BY ts_ms DESC
                LIMIT ?
                """,
                (action_norm, app_norm, title_norm, int(max(1, lookback))),
            ).fetchall()

        sample_size = len(rows)
        if sample_size == 0:
            return {
                "found": False,
                "action": action_norm,
                "app_name": app_norm,
                "window_title": title_norm,
                "sample_size": 0,
                "success_rate": None,
                "recommended_retries": 1,
                "recommended_pre_delay_ms": 0,
                "focus_before_action": False,
                "preferred_method": None,
                "reason": "no_history",
            }

        successes = sum(1 for r in rows if int(r[0]) == 1)
        success_rate = float(successes / max(1, sample_size))
        avg_duration = sum(float(r[4] or 0.0) for r in rows) / max(1, sample_size)

        reason_blob = " ".join(str(r[1] or "").lower() for r in rows[:20])
        needs_focus = any(token in reason_blob for token in ("focus", "window", "foreground", "mismatch"))
        loading_signals = any(token in reason_blob for token in ("loading", "stale", "timeout", "not_ready"))

        method_counts: dict[str, int] = {}
        for row in rows:
            method = str(row[5] or "").strip().lower()
            if not method:
                continue
            method_counts[method] = method_counts.get(method, 0) + 1
        preferred_method = None
        if method_counts:
            preferred_method = sorted(method_counts.items(), key=lambda item: item[1], reverse=True)[0][0]

        retries = 1
        if success_rate < 0.45:
            retries = 2
        elif success_rate > 0.85:
            retries = 0
        pre_delay_ms = 200 if loading_signals else 0

        return {
            "found": True,
            "action": action_norm,
            "app_name": app_norm,
            "window_title": title_norm,
            "sample_size": sample_size,
            "success_rate": round(success_rate, 3),
            "avg_duration_ms": round(avg_duration, 2),
            "recommended_retries": int(retries),
            "recommended_pre_delay_ms": int(pre_delay_ms),
            "focus_before_action": bool(needs_focus),
            "preferred_method": preferred_method,
            "reason": "history_based",
        }

    def recent(self, limit: int = 20) -> list[dict]:
        if not self._available or self._conn is None:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT ts_ms, app_name, window_title, action, success, reason, method_used, recovery_used, recovery_strategy, duration_ms
                FROM outcomes
                ORDER BY ts_ms DESC
                LIMIT ?
                """,
                (int(max(1, min(200, limit))),),
            ).fetchall()
        result = []
        for row in rows:
            result.append(
                {
                    "timestamp_ms": int(row[0]),
                    "app_name": row[1],
                    "window_title": row[2],
                    "action": row[3],
                    "success": bool(row[4]),
                    "reason": row[5],
                    "method_used": row[6],
                    "recovery_used": bool(row[7]),
                    "recovery_strategy": row[8],
                    "duration_ms": round(float(row[9] or 0.0), 2),
                }
            )
        return result

    def stats(self) -> dict:
        if not self._available or self._conn is None:
            return {"db_path": str(self._db_path), "entries": 0, "success_rate": 0.0,
                    "recovery_rate": 0.0, "distinct_apps": 0, "available": False}
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS ok,
                    SUM(CASE WHEN recovery_used = 1 THEN 1 ELSE 0 END) AS recovered
                FROM outcomes
                """
            ).fetchone()
            apps = self._conn.execute(
                "SELECT COUNT(DISTINCT app_name) FROM outcomes"
            ).fetchone()
        total = int((row[0] or 0) if row else 0)
        ok = int((row[1] or 0) if row else 0)
        recovered = int((row[2] or 0) if row else 0)
        return {
            "db_path": str(self._db_path),
            "entries": total,
            "success_rate": round(float(ok / max(1, total)), 3),
            "recovery_rate": round(float(recovered / max(1, total)), 3),
            "distinct_apps": int((apps[0] or 0) if apps else 0),
        }
