"""Append-only, bounded-memory SQLite journal. No automatic message deletion."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import threading


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.lock, closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, event_key TEXT NOT NULL, stage TEXT NOT NULL,
                record TEXT NOT NULL, UNIQUE(event_key, stage))""")

    def append(self, event_key, stage, record):
        encoded = json.dumps(record, ensure_ascii=False)
        with self.lock, closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("INSERT OR IGNORE INTO messages(event_key,stage,record) VALUES(?,?,?)",
                       (event_key, stage, encoded))
