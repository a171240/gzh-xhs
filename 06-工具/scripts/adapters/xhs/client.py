#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""XHS adapter client with strict security defaults."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .contract import ALLOWED_ACTIONS, ActionRequest, ActionResult
from .lock import AccountActionLock
from .security_guard import (
    SecurityError,
    env_truthy,
    ensure_profile_path,
    normalize_repo_path,
    redact_text,
    validate_redirect_chain,
    validate_url,
)


DEFAULT_CONFIG_PATH = Path("06-工具/scripts/config/xhs_adapter.json")
DEFAULT_LOCK_ROOT = Path("06-工具/data/automation/locks/xhs")
DEFAULT_PROFILE_ROOT = Path("06-工具/data/automation/profiles/xhs")


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return dict(data) if isinstance(data, dict) else {}


def _normalize_cmd(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item for item in shlex.split(value) if item]
    return []


class XHSAdapterClient:
    def __init__(self, *, config: dict[str, Any] | None = None, repo_root: Path | None = None) -> None:
        self.repo_root = Path(repo_root or Path.cwd()).resolve()
        self.config = dict(config or {})
        self.enabled = bool(self.config.get("enabled", False))
        self.profile_root = normalize_repo_path(self.repo_root, str(self.config.get("profile_root") or DEFAULT_PROFILE_ROOT))
        self.lock_root = normalize_repo_path(self.repo_root, str(self.config.get("lock_root") or DEFAULT_LOCK_ROOT))
        network_cfg = self.config.get("network") if isinstance(self.config.get("network"), dict) else {}
        cdp_cfg = self.config.get("cdp") if isinstance(self.config.get("cdp"), dict) else {}
        runner_cfg = self.config.get("runner") if isinstance(self.config.get("runner"), dict) else {}
        self.url_allowlist = list(network_cfg.get("url_allowlist") or [])
        self.block_private_ip = bool(network_cfg.get("block_private_ip", True))
        self.cdp_allow_remote = bool(cdp_cfg.get("allow_remote", False))
        self.cdp_allowed_hosts = [str(item).strip() for item in (cdp_cfg.get("allowed_hosts") or ["127.0.0.1"]) if str(item).strip()]
        self.allowed_binaries = [str(item).strip().lower() for item in (runner_cfg.get("allowed_binaries") or ["python", "python.exe", "python3", "py"]) if str(item).strip()]
        self.allowed_exec_roots = [
            normalize_repo_path(self.repo_root, str(item))
            for item in (runner_cfg.get("allowed_executable_roots") or [])
            if str(item).strip()
        ]

    @classmethod
    def from_env(cls, *, repo_root: Path | None = None) -> "XHSAdapterClient":
        base = Path(repo_root or Path.cwd()).resolve()
        config_path_env = str(os.getenv("XHS_ADAPTER_CONFIG") or "").strip()
        config_path = Path(config_path_env).resolve() if config_path_env else (base / DEFAULT_CONFIG_PATH).resolve()
        config = _load_json_file(config_path)
        if "enabled" not in config:
            config["enabled"] = env_truthy("XHS_ADAPTER_ENABLED", default=False)
        else:
            config["enabled"] = bool(config["enabled"]) and env_truthy("XHS_ADAPTER_ENABLED", default=True)
        return cls(config=config, repo_root=base)

    def execute(
        self,
        *,
        action: str,
        account_id: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str = "",
        dry_run: bool = False,
        timeout_sec: int = 180,
        trace_id: str = "",
    ) -> ActionResult:
        resolved_trace_id = str(trace_id or f"xhs_{action}_{account_id}")
        request = ActionRequest(
            action=action,
            account_id=account_id,
            payload=dict(payload or {}),
            idempotency_key=idempotency_key,
            dry_run=dry_run,
            timeout_sec=timeout_sec,
            trace_id=resolved_trace_id,
        )
        try:
            request.validate()
            if request.normalized_action() not in ALLOWED_ACTIONS:
                raise ValueError("unsupported action")
            if not self.enabled:
                raise SecurityError("xhs adapter is disabled")
            ensure_profile_path(self.profile_root, request.account_id)
            self._validate_network_payload(request.payload)
            self._validate_cdp_payload(request.payload)
            with AccountActionLock(
                lock_root=self.lock_root,
                account_id=request.account_id,
                action=request.normalized_action(),
                timeout_sec=min(float(request.timeout_sec), 10.0),
                stale_after_sec=float(self.config.get("lock_stale_after_sec") or 1800.0),
            ):
                if request.dry_run:
                    return ActionResult.success(
                        action=request.action,
                        account_id=request.account_id,
                        trace_id=request.trace_id,
                        status="dry_run",
                        data={
                            "validated": True,
                            "action": request.normalized_action(),
                            "account_id": request.normalized_account(),
                        },
                    )
                command = self._resolve_command(request.normalized_action())
                if not command:
                    raise RuntimeError(f"runner command not configured for action={request.normalized_action()}")
                return self._run_external(command=command, request=request)
        except Exception as exc:
            return ActionResult.failure(
                action=action,
                account_id=account_id,
                trace_id=resolved_trace_id,
                error=str(exc),
                status="error",
            )

    def _resolve_command(self, action: str) -> list[str]:
        env_specific = _normalize_cmd(os.getenv(f"XHS_ADAPTER_CMD_{action.upper()}"))
        if env_specific:
            return env_specific
        env_default = _normalize_cmd(os.getenv("XHS_ADAPTER_CMD"))
        if env_default:
            return env_default
        runner_cfg = self.config.get("runner") if isinstance(self.config.get("runner"), dict) else {}
        action_map = runner_cfg.get("action_commands") if isinstance(runner_cfg.get("action_commands"), dict) else {}
        mapped = _normalize_cmd(action_map.get(action))
        if mapped:
            return mapped
        return _normalize_cmd(runner_cfg.get("default_command"))

    def _enforce_command_policy(self, prepared: list[str]) -> None:
        if not prepared:
            raise SecurityError("runner command is empty")
        first = str(prepared[0] or "").strip()
        if not first:
            raise SecurityError("runner executable is empty")
        if self.allowed_binaries:
            exe_name = Path(first).name.strip().lower()
            exact = first.strip().lower()
            if exe_name not in self.allowed_binaries and exact not in self.allowed_binaries:
                raise SecurityError(f"runner binary is not allowed: {first}")
        if self.allowed_exec_roots:
            resolved = None
            first_path = Path(first)
            if first_path.is_absolute():
                resolved = first_path.resolve()
            else:
                discovered = shutil.which(first)
                if discovered:
                    resolved = Path(discovered).resolve()
            if resolved is None:
                raise SecurityError("runner executable could not be resolved for root validation")
            allowed = any(root == resolved or root in resolved.parents for root in self.allowed_exec_roots)
            if not allowed:
                raise SecurityError(f"runner executable is outside allowed roots: {resolved}")

    def _validate_network_payload(self, payload: dict[str, Any]) -> None:
        url_candidates: list[str] = []
        for key in ("url", "target_url", "detail_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                url_candidates.append(value.strip())
        urls = payload.get("urls")
        if isinstance(urls, list):
            url_candidates.extend([str(item).strip() for item in urls if str(item).strip()])
        redirect_chain = payload.get("redirect_chain")
        if isinstance(redirect_chain, list) and redirect_chain:
            validate_redirect_chain(
                [str(item).strip() for item in redirect_chain if str(item).strip()],
                allowlist=self.url_allowlist,
                block_private_ip=self.block_private_ip,
            )
        for item in url_candidates:
            validate_url(item, allowlist=self.url_allowlist, block_private_ip=self.block_private_ip)

    def _validate_cdp_payload(self, payload: dict[str, Any]) -> None:
        cdp = payload.get("cdp")
        if not isinstance(cdp, dict):
            return
        host = str(cdp.get("host") or "").strip()
        if not host:
            return
        if not self.cdp_allow_remote and host not in {"127.0.0.1", "localhost"}:
            raise SecurityError("remote CDP is disabled by policy")
        if self.cdp_allow_remote and self.cdp_allowed_hosts and host not in self.cdp_allowed_hosts:
            raise SecurityError("cdp host is not in allowlist")

    def _run_external(self, *, command: list[str], request: ActionRequest) -> ActionResult:
        payload = request.to_dict()
        tmp_dir = (self.repo_root / "06-工具" / "data" / "automation" / "tmp").resolve()
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_file_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                encoding="utf-8",
                delete=False,
                dir=tmp_dir,
            ) as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                tmp_file_path = Path(handle.name).resolve()
            mapping = {
                "request_file": str(tmp_file_path),
                "action": request.normalized_action(),
                "account": request.normalized_account(),
                "trace_id": request.trace_id,
            }
            prepared = [str(part).format(**mapping) for part in command]
            self._enforce_command_policy(prepared)
            completed = subprocess.run(
                prepared,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=int(request.timeout_sec),
                env=os.environ.copy(),
            )
            stdout_text = redact_text(completed.stdout or "")
            stderr_text = redact_text(completed.stderr or "")
            parsed = {}
            if stdout_text.strip():
                try:
                    loaded = json.loads(stdout_text)
                    if isinstance(loaded, dict):
                        parsed = loaded
                except Exception:
                    parsed = {}
            if completed.returncode == 0:
                result_data = {
                    "runner_command": prepared,
                    "stdout": stdout_text,
                    "stderr": stderr_text,
                    "parsed": parsed,
                }
                if parsed.get("ok") is False:
                    return ActionResult.failure(
                        action=request.action,
                        account_id=request.account_id,
                        trace_id=request.trace_id,
                        error=str(parsed.get("error") or "runner reported failure"),
                        status=str(parsed.get("status") or "error"),
                        data=result_data,
                    )
                return ActionResult.success(
                    action=request.action,
                    account_id=request.account_id,
                    trace_id=request.trace_id,
                    status=str(parsed.get("status") or "success"),
                    data=result_data,
                )
            return ActionResult.failure(
                action=request.action,
                account_id=request.account_id,
                trace_id=request.trace_id,
                error=f"runner exit_code={completed.returncode}",
                status="error",
                data={"stdout": stdout_text, "stderr": stderr_text, "runner_command": prepared},
            )
        finally:
            if tmp_file_path:
                try:
                    tmp_file_path.unlink(missing_ok=True)
                except Exception:
                    pass
