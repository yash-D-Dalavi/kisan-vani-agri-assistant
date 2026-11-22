"""
sessions.py - Module 1 "basic user session handling (language preference, last region, consent)" and Module 12 retention.

Phone numbers / user ids are stored only as a salted hash; a user can erase their session with a "delete my data" command.
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Dict, Optional

SALT = "agri-assistant-demo-salt"


def uid_hash(user_id: str) -> str:
    return hashlib.sha256((SALT + (user_id or "anon")).encode()).hexdigest()[:16]


class SessionStore:
    def __init__(self, retention_s: int = 30 * 24 * 3600):
        self._d: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self.retention_s = retention_s

    def get(self, user_id: Optional[str]) -> dict:
        if not user_id:
            return {}
        h = uid_hash(user_id)
        with self._lock:
            s = self._d.get(h)
            if s and time.time() - s["last_seen"] > self.retention_s:  # retention policy: expire inactive sessions
                del self._d[h]
                s = None
            if s is None:
                s = {"lang": None, "location": None, "consent": False, "created": time.time(), "last_seen": time.time(), "turns": 0}
                self._d[h] = s
            s["last_seen"] = time.time()
            return dict(s)

    def update(self, user_id: Optional[str], **kw) -> None:
        if not user_id:
            return
        self.get(user_id)
        with self._lock:
            self._d[uid_hash(user_id)].update(kw)

    def bump(self, user_id: Optional[str]) -> None:
        if user_id:
            with self._lock:
                s = self._d.get(uid_hash(user_id))
                if s:
                    s["turns"] += 1

    def delete(self, user_id: Optional[str]) -> bool:
        with self._lock:
            return self._d.pop(uid_hash(user_id or ""), None) is not None

    def count(self) -> int:
        with self._lock:
            return len(self._d)
