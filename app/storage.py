"""Persistência em SQLite: amostras (série temporal) e eventos (alertas/erros)."""
import os
import sqlite3
import threading

from .config import BASE_DIR

DB_PATH = os.environ.get("MONITOR_DB", os.path.join(BASE_DIR, "data", "monitor.db"))


class Storage:
    def __init__(self, path=DB_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
        CREATE TABLE IF NOT EXISTS samples(
            ts REAL PRIMARY KEY,          -- epoch (s, UTC)
            in_bps REAL, out_bps REAL, cpu REAL);
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL, kind TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
        """)
        self._db.commit()

    def add_sample(self, ts, in_bps, out_bps, cpu):
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO samples VALUES(?,?,?,?)",
                             (ts, in_bps, out_bps, cpu))
            self._db.commit()

    def add_event(self, ts, kind, level, message):
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events(ts,kind,level,message) VALUES(?,?,?,?)",
                (ts, kind, level, message))
            self._db.commit()
            return cur.lastrowid

    def history(self, start, end, max_points=1500):
        """Amostras em [start, end]. Se houver mais que max_points, agrupa por média
        (apenas para não travar o gráfico); `total` informa a contagem real."""
        with self._lock:
            total, first, last = self._db.execute(
                "SELECT COUNT(*),MIN(ts),MAX(ts) FROM samples WHERE ts BETWEEN ? AND ?",
                (start, end)).fetchone()
            if total == 0:
                return {"total": 0, "points": [], "first": None, "last": None, "bucket_s": 0}
            if total <= max_points:
                rows = self._db.execute(
                    "SELECT ts,in_bps,out_bps,cpu FROM samples WHERE ts BETWEEN ? AND ? ORDER BY ts",
                    (start, end)).fetchall()
                bucket = 0
            else:
                bucket = max(1.0, (end - start) / max_points)
                rows = self._db.execute(
                    "SELECT MIN(ts),AVG(in_bps),AVG(out_bps),AVG(cpu) FROM samples "
                    "WHERE ts BETWEEN ? AND ? GROUP BY CAST((ts-?)/? AS INTEGER) ORDER BY 1",
                    (start, end, start, bucket)).fetchall()
        return {"total": total, "first": first, "last": last, "bucket_s": bucket,
                "points": [list(r) for r in rows]}

    def events(self, limit=100):
        with self._lock:
            rows = self._db.execute(
                "SELECT id,ts,kind,level,message FROM events ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(id=r[0], ts=r[1], kind=r[2], level=r[3], message=r[4]) for r in rows]
