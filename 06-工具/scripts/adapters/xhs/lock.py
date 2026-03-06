#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-host lock for account+action execution."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .security_guard import ensure_lock_root, safe_join, validate_account_id


@dataclass(slots=True)
class AccountActionLock:
    lock_root: str | Path
    account_id: str
    action: str
    timeout_sec: float = 10.0
    poll_interval_sec: float = 0.2
    stale_after_sec: float = 1800.0
    _fd: int | None = field(init=False, default=None, repr=False)
    _lock_path: Path = field(init=False, repr=False)

    def __post_init__(self) -> None:
        root = ensure_lock_root(self.lock_root)
        account = validate_account_id(self.account_id)
        action = str(self.action or "").strip().lower()
        if not action:
            raise ValueError("action is required")
        self._lock_path = safe_join(root, f"{account}.{action}.lock")

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def _pid_alive(self, pid: int) -> bool:
        if int(pid) <= 0:
            return False
        try:
            os.kill(int(pid), 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False

    def _read_lock_meta(self) -> dict[str, object]:
        try:
            raw = self._lock_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception:
            return {}
        return {}

    def _stale_lock(self) -> bool:
        meta = self._read_lock_meta()
        created_at = int(meta.get("created_at") or 0)
        pid = int(meta.get("pid") or 0)
        now = int(time.time())
        if created_at > 0 and float(self.stale_after_sec) > 0:
            if now - created_at >= int(self.stale_after_sec):
                return True
        if pid > 0 and not self._pid_alive(pid):
            return True
        return False

    def _try_break_stale_lock(self) -> bool:
        if not self._lock_path.exists():
            return True
        if not self._stale_lock():
            return False
        try:
            self._lock_path.unlink(missing_ok=True)
            return True
        except Exception:
            return False

    def acquire(self) -> bool:
        deadline = time.monotonic() + max(float(self.timeout_sec), 0.0)
        payload = {
            "pid": os.getpid(),
            "account_id": self.account_id,
            "action": self.action,
            "created_at": int(time.time()),
        }
        while True:
            try:
                fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps(payload, ensure_ascii=True).encode("utf-8"))
                self._fd = fd
                return True
            except FileExistsError:
                self._try_break_stale_lock()
                if time.monotonic() >= deadline:
                    return False
                time.sleep(max(float(self.poll_interval_sec), 0.05))

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            self._lock_path.unlink(missing_ok=True)
        except Exception:
            pass

    def __enter__(self) -> "AccountActionLock":
        ok = self.acquire()
        if not ok:
            raise TimeoutError(f"lock timeout: {self._lock_path}")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
