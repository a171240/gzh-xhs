#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily metrics collection runner."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from automation_state import (
    METRICS_DIR,
    REPO_ROOT,
    add_task_log,
    append_dead_letter,
    append_run_log,
    create_task,
    list_tasks,
    make_task_id,
    update_task,
)
from feishu_http_client import safe_sync_metrics_record


WECHAT_MD = REPO_ROOT / "04-数据与方法论" / "内容数据统计" / "公众号数据.md"
XHS_MD = REPO_ROOT / "04-数据与方法论" / "内容数据统计" / "小红书数据.md"
DOUYIN_MD = REPO_ROOT / "04-数据与方法论" / "内容数据统计" / "抖音数据.md"


def _shanghai_today() -> str:
    try:
        tz = ZoneInfo("Asia/Shanghai")
        return dt.datetime.now(tz).date().isoformat()
    except Exception:
        return dt.date.today().isoformat()


def _normalize_date(raw: str) -> str:
    text = str(raw or "").strip().lower()
    if not text or text == "today":
        return _shanghai_today()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        raise ValueError("date must be YYYY-MM-DD or today")
    return text


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _request_json(url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    timeout_sec = max(5, int(os.getenv("METRICS_HTTP_TIMEOUT_SEC", "20")))
    verify_ssl = str(os.getenv("METRICS_HTTP_VERIFY_SSL", "true")).strip().lower() in {"1", "true", "yes", "y", "on"}
    response = requests.get(url, params=params or {}, headers=headers or {}, timeout=timeout_sec, verify=verify_ssl)
    if response.status_code >= 400:
        raise RuntimeError(f"http {response.status_code}: {response.text[:1000]}")
    try:
        return response.json()
    except Exception as exc:
        raise RuntimeError(f"invalid json from {url}: {exc}") from exc


def _extract_publish_records(date_str: str) -> list[dict[str, Any]]:
    rows = list_tasks(task_type="publish_prepare", date_prefix=date_str, limit=2000)
    records: list[dict[str, Any]] = []
    for task in rows:
        status = str(task.get("status") or "").strip()
        if status not in {"success", "waiting_manual_publish", "pending_approval", "error"}:
            continue
        platform = str(task.get("platform") or "").strip().lower()
        result = task.get("result_json") or {}
        approve = result.get("approve") if isinstance(result, dict) else {}
        approve = approve or {}

        content_url = ""
        if platform == "wechat":
            content_url = str(approve.get("publish_url") or approve.get("draft_url") or "").strip()
        elif platform == "xhs":
            content_url = str(approve.get("note_url") or "").strip()
        elif platform == "douyin":
            content_url = str(approve.get("publish_url") or approve.get("video_url") or "").strip()

        record = {
            "date": date_str,
            "platform": platform,
            "account": str(task.get("account") or ""),
            "task_id": str(task.get("task_id") or ""),
            "status": status,
            "views": int(approve.get("views") or 0),
            "play": int(approve.get("play") or 0),
            "likes": int(approve.get("likes") or 0),
            "comments": int(approve.get("comments") or 0),
            "shares": int(approve.get("shares") or 0),
            "collects": int(approve.get("collects") or approve.get("favorites") or 0),
            "source": "ui_fallback",
            "content_url": content_url,
        }
        records.append(record)
    return records


def _load_official_records(platform: str, date_str: str) -> tuple[list[dict[str, Any]], str]:
    key = platform.upper()
    api_url = str(os.getenv(f"{key}_METRICS_API_URL") or "").strip()
    if not api_url:
        return [], "api_not_configured"

    headers: dict[str, str] = {}
    bearer = str(os.getenv(f"{key}_METRICS_API_BEARER") or "").strip()
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"

    payload = _request_json(api_url, params={"date": date_str}, headers=headers)
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return [], "api_invalid_data"

    out: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "date": date_str,
                "platform": platform,
                "account": str(item.get("account") or item.get("账号") or ""),
                "task_id": str(item.get("task_id") or item.get("内容ID") or ""),
                "status": str(item.get("status") or "success"),
                "views": int(item.get("views") or item.get("阅读") or 0),
                "play": int(item.get("play") or item.get("播放") or 0),
                "likes": int(item.get("likes") or item.get("点赞") or 0),
                "comments": int(item.get("comments") or item.get("评论") or 0),
                "shares": int(item.get("shares") or item.get("分享") or 0),
                "collects": int(item.get("collects") or item.get("收藏") or 0),
                "source": "official_api",
                "content_url": str(item.get("content_url") or item.get("url") or ""),
            }
        )
    return out, "ok"


def _merge_metrics(date_str: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    fallback = _extract_publish_records(date_str)
    by_platform: dict[str, list[dict[str, Any]]] = {"wechat": [], "xhs": [], "douyin": []}

    for platform in by_platform:
        official, status = _load_official_records(platform, date_str)
        if official:
            by_platform[platform] = official
        else:
            by_platform[platform] = [item for item in fallback if item.get("platform") == platform]
            for item in by_platform[platform]:
                item["source"] = "ui_fallback" if status != "ok" else item.get("source")

    rows: list[dict[str, Any]] = []
    for platform in ("wechat", "xhs", "douyin"):
        rows.extend(by_platform[platform])

    summary = {
        "date": date_str,
        "total": len(rows),
        "wechat": len(by_platform["wechat"]),
        "xhs": len(by_platform["xhs"]),
        "douyin": len(by_platform["douyin"]),
    }
    return rows, summary


def _write_json_csv(date_str: str, rows: list[dict[str, Any]]) -> tuple[str, str]:
    out_dir = METRICS_DIR / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "metrics.json"
    csv_path = out_dir / "metrics.csv"

    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    fields = [
        "date",
        "platform",
        "account",
        "task_id",
        "status",
        "views",
        "play",
        "likes",
        "comments",
        "shares",
        "collects",
        "source",
        "content_url",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fw:
        writer = csv.DictWriter(fw, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})

    return json_path.relative_to(REPO_ROOT).as_posix(), csv_path.relative_to(REPO_ROOT).as_posix()


def _render_table_rows(rows: list[dict[str, Any]], *, platform: str) -> list[str]:
    out: list[str] = []
    for row in rows:
        if str(row.get("platform") or "") != platform:
            continue
        read_or_play = int(row.get("views") or 0) or int(row.get("play") or 0)
        out.append(
            "| {account} | {task_id} | {status} | {read_or_play} | {likes} | {comments} | {shares} | {collects} | {source} | {url} |".format(
                account=str(row.get("account") or "-"),
                task_id=str(row.get("task_id") or "-")[:32],
                status=str(row.get("status") or "-"),
                read_or_play=read_or_play,
                likes=int(row.get("likes") or 0),
                comments=int(row.get("comments") or 0),
                shares=int(row.get("shares") or 0),
                collects=int(row.get("collects") or 0),
                source=str(row.get("source") or "-"),
                url=str(row.get("content_url") or "-"),
            )
        )
    return out


def _append_platform_markdown(path: Path, *, date_str: str, rows: list[dict[str, Any]], platform: str, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    block: list[str] = [
        "",
        f"## {date_str} 自动抓数",
        f"- 平台: {title}",
        "| 账号 | 任务ID | 状态 | 阅读/播放 | 点赞 | 评论 | 分享 | 收藏 | 来源 | 链接 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    platform_rows = _render_table_rows(rows, platform=platform)
    if platform_rows:
        block.extend(platform_rows)
    else:
        block.append("| - | - | no_data | 0 | 0 | 0 | 0 | 0 | - | - |")

    with path.open("a", encoding="utf-8") as fw:
        fw.write("\n".join(block) + "\n")


def _sync_to_feishu(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = 0
    failed = 0
    details: list[dict[str, Any]] = []
    for row in rows:
        fields = {
            "日期": row.get("date"),
            "平台": row.get("platform"),
            "账号": row.get("account"),
            "任务ID": row.get("task_id"),
            "状态": row.get("status"),
            "阅读": row.get("views") or row.get("play") or 0,
            "点赞": row.get("likes") or 0,
            "评论": row.get("comments") or 0,
            "分享": row.get("shares") or 0,
            "收藏": row.get("collects") or 0,
            "链接": row.get("content_url") or "",
            "来源": row.get("source") or "",
        }
        resp = safe_sync_metrics_record(fields)
        details.append(resp)
        if resp.get("ok"):
            ok += 1
        elif resp.get("skipped"):
            # Treat skipped as neutral in local runs.
            pass
        else:
            failed += 1
    return {"ok": ok, "failed": failed, "details": details}


def run_metrics(payload: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    date_str = _normalize_date(str(payload.get("date") or payload.get("日期") or "today"))
    event_ref = str(payload.get("event_ref") or "").strip()
    source_user = str(payload.get("source_user") or "").strip()

    task_id = make_task_id("metrics")
    create_task(
        task_id=task_id,
        event_ref=event_ref,
        task_type="metrics_run",
        status="running",
        phase="collect",
        platform="all",
        source_user=source_user,
        payload={"request": payload, "date": date_str},
        result={"status": "running"},
    )

    try:
        rows, summary = _merge_metrics(date_str)
        if dry_run:
            result = {"status": "dry_run", "date": date_str, "summary": summary, "rows": rows}
            update_task(task_id, status="success", phase="collect", result_json=_json_dumps(result), error_text="")
            return {"status": "success", "task_id": task_id, "dry_run": True, "result": result}

        json_rel, csv_rel = _write_json_csv(date_str, rows)
        _append_platform_markdown(WECHAT_MD, date_str=date_str, rows=rows, platform="wechat", title="公众号")
        _append_platform_markdown(XHS_MD, date_str=date_str, rows=rows, platform="xhs", title="小红书")
        _append_platform_markdown(DOUYIN_MD, date_str=date_str, rows=rows, platform="douyin", title="抖音")
        sync_result = _sync_to_feishu(rows)

        result = {
            "status": "success",
            "date": date_str,
            "summary": summary,
            "json_path": json_rel,
            "csv_path": csv_rel,
            "records": rows,
            "feishu_sync": sync_result,
        }
        update_task(task_id, status="success", phase="completed", result_json=_json_dumps(result), error_text="")
        add_task_log(task_id, "metrics_completed", {"summary": summary, "sync": sync_result})
        run_log = append_run_log("metrics_run", {"task_id": task_id, "date": date_str, "summary": summary})
        return {
            "status": "success",
            "task_id": task_id,
            "phase": "completed",
            "date": date_str,
            "run_log": run_log,
            "result": result,
        }

    except Exception as exc:
        error_text = str(exc)
        update_task(task_id, status="error", phase="collect", error_text=error_text)
        add_task_log(task_id, "metrics_failed", {"error": error_text})
        dead_log = append_dead_letter("metrics_run_failed", {"task_id": task_id, "date": date_str, "error": error_text})
        return {
            "status": "error",
            "task_id": task_id,
            "phase": "collect",
            "date": date_str,
            "errors": [error_text],
            "dead_letter_log": dead_log,
        }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Metrics runner")
    parser.add_argument("--date", default="today", help="YYYY-MM-DD or today")
    parser.add_argument("--payload-file", default="", help="optional payload JSON")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    payload: dict[str, Any] = {"date": args.date}
    if args.payload_file:
        raw = json.loads(Path(args.payload_file).read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict):
            payload.update(raw)
    result = run_metrics(payload, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if str(result.get("status") or "") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main(os.sys.argv[1:]))
