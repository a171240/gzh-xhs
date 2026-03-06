#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Security guardrails for XHS adapter boundary."""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


ACCOUNT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
METADATA_HOSTS = {"169.254.169.254", "metadata.google.internal"}
SENSITIVE_QUERY_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "auth",
    "cookie",
    "session",
    "sessionid",
    "password",
    "passwd",
    "secret",
    "apikey",
    "api_key",
}


class SecurityError(ValueError):
    """Raised on policy validation failures."""


def validate_account_id(value: str) -> str:
    account_id = str(value or "").strip()
    if not ACCOUNT_ID_PATTERN.fullmatch(account_id):
        raise SecurityError("account_id must match [a-zA-Z0-9_-]{1,64}")
    return account_id


def _resolve_path(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def is_within_root(root: str | Path, target: str | Path) -> bool:
    root_path = _resolve_path(root)
    target_path = _resolve_path(target)
    if target_path == root_path:
        return True
    return root_path in target_path.parents


def safe_join(root: str | Path, *parts: str) -> Path:
    root_path = _resolve_path(root)
    candidate = root_path.joinpath(*parts).resolve()
    if not is_within_root(root_path, candidate):
        raise SecurityError("path escapes controlled root")
    return candidate


def ensure_safe_delete(
    *,
    root: str | Path,
    target: str | Path,
    require_confirmation: bool = False,
    confirm_token: str = "",
    expected_token: str = "DELETE",
) -> Path:
    root_path = _resolve_path(root)
    target_path = _resolve_path(target)
    if target_path == root_path:
        raise SecurityError("refuse deleting profile root")
    if target_path == Path(target_path.anchor):
        raise SecurityError("refuse deleting filesystem root")
    if not is_within_root(root_path, target_path):
        raise SecurityError("target path escapes controlled root")
    if require_confirmation and str(confirm_token or "").strip() != expected_token:
        raise SecurityError("delete confirmation token mismatch")
    return target_path


def _host_allowed(host: str, allowlist: Iterable[str]) -> bool:
    host_l = host.casefold()
    patterns = [str(item or "").strip().casefold() for item in allowlist if str(item or "").strip()]
    if not patterns:
        return True
    for item in patterns:
        if item.startswith("*."):
            suffix = item[2:]
            if host_l == suffix or host_l.endswith(f".{suffix}"):
                return True
        elif host_l == item:
            return True
    return False


def _is_blocked_ip(addr: ipaddress._BaseAddress) -> bool:
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _resolve_ips(host: str) -> set[ipaddress._BaseAddress]:
    resolved: set[ipaddress._BaseAddress] = set()
    for family, _, _, _, sockaddr in socket.getaddrinfo(host, None):
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        raw_ip = sockaddr[0]
        resolved.add(ipaddress.ip_address(raw_ip))
    return resolved


def validate_url(url: str, *, allowlist: Iterable[str] | None = None, block_private_ip: bool = True) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise SecurityError("only http/https URLs are allowed")
    host = str(parsed.hostname or "").strip()
    if not host:
        raise SecurityError("URL host is required")
    if host in METADATA_HOSTS:
        raise SecurityError("metadata endpoint is blocked")
    if allowlist and not _host_allowed(host, allowlist):
        raise SecurityError("host is not in network allowlist")
    try:
        addr = ipaddress.ip_address(host)
        ips = {addr}
    except ValueError:
        ips = _resolve_ips(host)
    if block_private_ip:
        for ip_item in ips:
            if str(ip_item) == "169.254.169.254" or _is_blocked_ip(ip_item):
                raise SecurityError("private/local/reserved address is blocked")
    return urlunparse(parsed._replace(fragment=""))


def validate_redirect_chain(
    urls: Iterable[str],
    *,
    allowlist: Iterable[str] | None = None,
    block_private_ip: bool = True,
) -> list[str]:
    validated: list[str] = []
    for item in urls:
        validated.append(validate_url(item, allowlist=allowlist, block_private_ip=block_private_ip))
    return validated


def redact_url(raw_url: str) -> str:
    parsed = urlparse(str(raw_url or "").strip())
    if not parsed.query:
        return raw_url
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    replaced = []
    for key, value in pairs:
        if str(key or "").casefold() in SENSITIVE_QUERY_KEYS:
            replaced.append((key, "***"))
        else:
            replaced.append((key, value))
    return urlunparse(parsed._replace(query=urlencode(replaced, doseq=True)))


def redact_text(raw: str) -> str:
    text = str(raw or "")
    text = re.sub(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+", r"\1***", text)
    text = re.sub(r"(?i)(cookie\s*:\s*)[^\n\r]+", r"\1***", text)
    text = re.sub(r"(?i)(token=)[^&\s]+", r"\1***", text)
    return text


def ensure_profile_path(profile_root: str | Path, account_id: str) -> Path:
    account = validate_account_id(account_id)
    return safe_join(profile_root, account)


def ensure_lock_root(path_value: str | Path) -> Path:
    lock_root = _resolve_path(path_value)
    lock_root.mkdir(parents=True, exist_ok=True)
    return lock_root


def normalize_repo_path(repo_root: str | Path, value: str) -> Path:
    if not str(value or "").strip():
        return _resolve_path(repo_root)
    candidate = Path(value)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = _resolve_path(Path(repo_root) / candidate)
    return resolved


def env_truthy(name: str, *, default: bool = False) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}
