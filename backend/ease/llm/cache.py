"""On-disk LLM response cache.

readwrite - reuse identical calls (re-running a task during development costs no quota)
replay    - read only; a miss is an error (deterministic CI / reproducing a benchmark run)
off       - always call the model (live benchmark runs)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class CacheMiss(Exception):
    pass


class LlmCache:
    def __init__(self, path: Path, mode: str):
        self.mode = mode
        self.path = path
        self._lock = threading.Lock()
        if mode != "off":
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._conn() as c:
                c.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT NOT NULL, at REAL NOT NULL)")

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    @staticmethod
    def key(payload: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        if self.mode == "off":
            return None
        with self._lock, self._conn() as c:
            row = c.execute("SELECT v FROM cache WHERE k = ?", (key,)).fetchone()
        if row is None:
            if self.mode == "replay":
                raise CacheMiss(key)
            return None
        return json.loads(row[0])

    def put(self, key: str, value: dict[str, Any]) -> None:
        if self.mode != "readwrite":
            return
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, json.dumps(value), time.time()))
