#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Auto commit/push content changes after Feishu ingest/skill writes."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class GitSyncSettings:
    enabled: bool
    repo_root: Path
    remote: str
    branch: str
    include_paths: tuple[str, ...]
    author_name: str
    author_email: str
    max_retries: int


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _split_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    out: list[str] = []
    for raw in str(value).replace(";", ",").split(","):
        item = raw.strip().replace("\\", "/").strip("/")
        if not item:
            continue
        out.append(item)
    return tuple(dict.fromkeys(out))


def _default_include_paths(repo_root: Path) -> tuple[str, ...]:
    preferred = ("02-内容生产", "03-素材库", "01-选题管理")
    existing = tuple(name for name in preferred if (repo_root / name).exists())
    if existing:
        return existing

    fallback: list[str] = []
    for prefix in ("02-", "03-", "01-"):
        hit = next((p.name for p in repo_root.iterdir() if p.is_dir() and p.name.startswith(prefix)), "")
        if hit:
            fallback.append(hit)
    return tuple(fallback)


def _load_settings() -> GitSyncSettings:
    repo_root = Path(os.getenv("GIT_SYNC_REPO_ROOT") or _repo_root()).resolve()
    include = _split_csv(os.getenv("GIT_SYNC_INCLUDE_PATHS")) or _default_include_paths(repo_root)
    return GitSyncSettings(
        enabled=_as_bool(os.getenv("GIT_SYNC_ENABLED"), default=False),
        repo_root=repo_root,
        remote=str(os.getenv("GIT_SYNC_REMOTE") or "origin").strip() or "origin",
        branch=str(os.getenv("GIT_SYNC_BRANCH") or "main").strip() or "main",
        include_paths=include,
        author_name=str(os.getenv("GIT_SYNC_AUTHOR_NAME") or "feishu-bot").strip() or "feishu-bot",
        author_email=str(os.getenv("GIT_SYNC_AUTHOR_EMAIL") or "feishu-bot@local").strip() or "feishu-bot@local",
        max_retries=max(0, int(os.getenv("GIT_SYNC_MAX_RETRIES") or "2")),
    )


def _run(
    args: list[str],
    *,
    cwd: Path,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if check and completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        msg = stderr or stdout or f"command failed: {' '.join(args)}"
        raise RuntimeError(msg)
    return completed


def _normalize_rel_path(repo_root: Path, path: str) -> str:
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        return ""
    p = Path(raw)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(repo_root)
        except Exception:
            return ""
    rel = p.as_posix().lstrip("./")
    return rel


def _is_allowed_path(rel_path: str, include_paths: tuple[str, ...]) -> bool:
    for root in include_paths:
        base = root.strip("/").replace("\\", "/")
        if not base:
            continue
        if rel_path == base or rel_path.startswith(base + "/"):
            return True
        if base.endswith("-") and rel_path.startswith(base):
            return True
    return False


def _checkout_branch(settings: GitSyncSettings) -> None:
    _run(["git", "fetch", settings.remote, settings.branch], cwd=settings.repo_root, check=False)
    current = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=settings.repo_root).stdout.strip()
    if current == settings.branch:
        return
    switched = _run(["git", "checkout", settings.branch], cwd=settings.repo_root, check=False)
    if switched.returncode == 0:
        return
    _run(
        ["git", "checkout", "-B", settings.branch, f"{settings.remote}/{settings.branch}"],
        cwd=settings.repo_root,
    )


def _collect_changed_paths(settings: GitSyncSettings) -> list[str]:
    completed = _run(
        ["git", "diff", "--cached", "--name-only", "--", *settings.include_paths],
        cwd=settings.repo_root,
        check=False,
    )
    paths = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    return sorted(dict.fromkeys(paths))


def run_sync(*, event_ref: str, kind: str, paths: list[str], dry_run: bool) -> dict[str, Any]:
    settings = _load_settings()
    result: dict[str, Any] = {
        "status": "skipped",
        "event_ref": event_ref,
        "kind": kind,
        "repo_root": settings.repo_root.as_posix(),
        "branch": settings.branch,
        "remote": settings.remote,
        "changed_paths": [],
        "commit": "",
        "message": "",
    }

    if not settings.enabled:
        result["message"] = "GIT_SYNC_ENABLED=false"
        return result

    if not (settings.repo_root / ".git").exists():
        result["status"] = "error"
        result["message"] = f"not a git repo: {settings.repo_root}"
        return result

    if not settings.include_paths:
        result["status"] = "error"
        result["message"] = "no include paths configured"
        return result

    _checkout_branch(settings)

    normalized = [_normalize_rel_path(settings.repo_root, p) for p in paths]
    allowed = [p for p in normalized if p and _is_allowed_path(p, settings.include_paths)]
    add_targets = allowed or list(settings.include_paths)

    _run(["git", "add", "--", *add_targets], cwd=settings.repo_root)
    changed = _collect_changed_paths(settings)
    result["changed_paths"] = changed
    if not changed:
        result["message"] = "no staged changes in include paths"
        return result

    if dry_run:
        result["status"] = "dry_run"
        result["message"] = "staged only, skip commit/push"
        return result

    message = f"feishu-sync: {event_ref} {kind}"
    git_env = os.environ.copy()
    git_env["GIT_AUTHOR_NAME"] = settings.author_name
    git_env["GIT_AUTHOR_EMAIL"] = settings.author_email
    git_env["GIT_COMMITTER_NAME"] = settings.author_name
    git_env["GIT_COMMITTER_EMAIL"] = settings.author_email
    _run(["git", "commit", "--no-gpg-sign", "-m", message], cwd=settings.repo_root, env=git_env)
    head = _run(["git", "rev-parse", "HEAD"], cwd=settings.repo_root).stdout.strip()
    result["commit"] = head
    result["message"] = message

    attempts = settings.max_retries + 1
    for idx in range(1, attempts + 1):
        pushed = _run(["git", "push", settings.remote, settings.branch], cwd=settings.repo_root, check=False)
        if pushed.returncode == 0:
            result["status"] = "success"
            result["attempts"] = idx
            return result

        if idx >= attempts:
            result["status"] = "error"
            result["message"] = (pushed.stderr or pushed.stdout or "").strip() or "git push failed"
            return result

        pulled = _run(
            ["git", "pull", "--rebase", settings.remote, settings.branch],
            cwd=settings.repo_root,
            check=False,
        )
        if pulled.returncode != 0:
            _run(["git", "rebase", "--abort"], cwd=settings.repo_root, check=False)
            result["status"] = "error"
            result["message"] = (pulled.stderr or pulled.stdout or "").strip() or "git pull --rebase failed"
            return result
        time.sleep(1)

    result["status"] = "error"
    result["message"] = "unexpected push loop exit"
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto sync content changes to git remote.")
    parser.add_argument("--event-ref", required=True, help="Feishu event reference")
    parser.add_argument("--kind", default="manual", help="ingest|skill|mixed|manual")
    parser.add_argument("--path", dest="paths", action="append", default=[], help="Changed relative path")
    parser.add_argument("--dry-run", action="store_true", help="Stage only, do not commit/push")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    payload = run_sync(
        event_ref=str(args.event_ref),
        kind=str(args.kind or "manual"),
        paths=[str(p) for p in (args.paths or [])],
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") in {"success", "skipped", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main(os.sys.argv[1:]))
