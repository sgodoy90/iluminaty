"""
ILUMINATY - Capa 7: Audit Log Persistente
==========================================
Cada accion del agente queda registrada: que, cuando, por que, resultado.
Inmutable una vez escrito. SQLite local con rotacion.

A diferencia del ring buffer visual (RAM-only), el audit log SI persiste
en disco porque es un requisito de compliance y debugging.
"""

import json
import time
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class AuditEntry:
    """Una entrada en el audit log."""
    timestamp: float
    action: str
    category: str
    params: dict
    result: str  # "success", "failed", "rejected", "expired", "blocked"
    message: str
    autonomy_level: str
    app_context: Optional[str] = None
    duration_ms: float = 0.0
    entry_id: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "id": self.entry_id,
            "timestamp": self.timestamp,
            "time_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.timestamp)),
            "action": self.action,
            "category": self.category,
            "params": self.params,
            "result": self.result,
            "message": self.message,
            "autonomy_level": self.autonomy_level,
            "app_context": self.app_context,
            "duration_ms": self.duration_ms,
        }


class AuditLog:
    """
    Audit log persistente en SQLite.

    Thread-safe. Auto-rotation cuando supera max_entries.
    Solo append, nunca delete (inmutable por diseño).
    """

    def __init__(self, db_path: Optional[str] = None, max_entries: int = 50000):
        if db_path is None:
            audit_dir = Path.home() / ".iluminaty"
            audit_dir.mkdir(exist_ok=True)
            db_path = str(audit_dir / "audit.db")

        self._db_path = db_path
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._total_logged = 0
        self._init_db()

    def _init_db(self):
        """Crea la tabla si no existe."""
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    action TEXT NOT NULL,
                    category TEXT NOT NULL,
                    params TEXT NOT NULL,
                    result TEXT NOT NULL,
                    message TEXT NOT NULL,
                    autonomy_level TEXT NOT NULL,
                    app_context TEXT,
                    duration_ms REAL DEFAULT 0.0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_result ON audit_log(result)
            """)
            # Contar entradas existentes
            cursor = conn.execute("SELECT COUNT(*) FROM audit_log")
            self._total_logged = cursor.fetchone()[0]
            conn.commit()
            conn.close()

    def log(self, action: str, category: str, params: dict, result: str,
            message: str, autonomy_level: str, app_context: Optional[str] = None,
            duration_ms: float = 0.0) -> AuditEntry:
        """Registra una accion en el audit log."""
        entry = AuditEntry(
            timestamp=time.time(),
            action=action,
            category=category,
            params=params,
            result=result,
            message=message,
            autonomy_level=autonomy_level,
            app_context=app_context,
            duration_ms=duration_ms,
        )

        with self._lock:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.execute(
                """INSERT INTO audit_log
                   (timestamp, action, category, params, result, message, autonomy_level, app_context, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (entry.timestamp, entry.action, entry.category,
                 json.dumps(entry.params), entry.result, entry.message,
                 entry.autonomy_level, entry.app_context, entry.duration_ms)
            )
            entry.entry_id = cursor.lastrowid
            self._total_logged += 1
            conn.commit()

            # Rotacion: eliminar entradas antiguas si excede max
            if self._total_logged > self._max_entries:
                keep_from = self._total_logged - self._max_entries
                conn.execute("DELETE FROM audit_log WHERE id <= ?", (keep_from,))
                conn.commit()
                self._total_logged = self._max_entries

            conn.close()

        return entry

    def query(self, action: Optional[str] = None, result: Optional[str] = None,
              since: Optional[float] = None, limit: int = 50) -> list[dict]:
        """Consulta el audit log con filtros."""
        conditions = []
        params = []

        if action:
            conditions.append("action = ?")
            params.append(action)
        if result:
            conditions.append("result = ?")
            params.append(result)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._lock:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT ?",
                params + [limit]
            )
            rows = cursor.fetchall()
            conn.close()

        entries = []
        for row in rows:
            entries.append(AuditEntry(
                entry_id=row["id"],
                timestamp=row["timestamp"],
                action=row["action"],
                category=row["category"],
                params=json.loads(row["params"]),
                result=row["result"],
                message=row["message"],
                autonomy_level=row["autonomy_level"],
                app_context=row["app_context"],
                duration_ms=row["duration_ms"],
            ).to_dict())

        return entries

    def get_recent(self, count: int = 20) -> list[dict]:
        """Ultimas N entradas."""
        return self.query(limit=count)

    def get_failures(self, count: int = 20) -> list[dict]:
        """Ultimas acciones fallidas."""
        return self.query(result="failed", limit=count)

    @property
    def stats(self) -> dict:
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
            successes = conn.execute("SELECT COUNT(*) FROM audit_log WHERE result='success'").fetchone()[0]
            failures = conn.execute("SELECT COUNT(*) FROM audit_log WHERE result='failed'").fetchone()[0]
            rejected = conn.execute("SELECT COUNT(*) FROM audit_log WHERE result='rejected'").fetchone()[0]
            conn.close()

        return {
            "total_entries": total,
            "successes": successes,
            "failures": failures,
            "rejected": rejected,
            "success_rate": round(successes / max(total, 1) * 100, 1),
            "db_path": self._db_path,
            "max_entries": self._max_entries,
        }
