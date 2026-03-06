#!/usr/bin/env python3
"""
Compatibility crawl bridge for desktop-app.

Supports:
- URL crawl (via 内容抓取/url-reader)
- Keyword matrix crawl (internal script with external fallback)
- Dual mode (URL + keyword)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
CRAWL_ROOT = ROOT / "内容抓取" / "抓取内容"
URL_READER_ROOT = ROOT / "内容抓取" / "url-reader"
URL_READER_SCRIPT_DIR = URL_READER_ROOT / "scripts"
URL_READER_VENV_PY = URL_READER_ROOT / ".venv" / "Scripts" / "python.exe"
INTERNAL_KEYWORD_SCRIPT = ROOT / "scripts" / "crawlers" / "keyword_matrix.py"
EXTERNAL_KEYWORD_SCRIPT = (
    Path(os.environ.get("USERPROFILE") or str(Path.home()))
    / ".codex"
    / "skills"
    / "keyword-crawler-matrix"
    / "scripts"
    / "crawl_keywords.py"
)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def safe_name(value: str, max_len: int = 40) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fa5_-]+", "-", str(value or "").strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned[:max_len] or "item"


def as_bool(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on", "是"}


def split_lines(value: Any) -> List[str]:
    text = str(value or "")
    if not text.strip():
        return []
    rows: List[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in re.split(r"[，,;；\s]+", line) if part.strip()]
        if len(parts) == 1:
            rows.append(parts[0])
        else:
            rows.extend(parts)
    return rows


def parse_brief_lines(brief: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in str(brief or "").replace("\r\n", "\n").split("\n"):
        item = line.strip()
        if not item:
            continue
        match = re.match(r"^([^：:]+)[：:](.*)$", item)
        if not match:
            continue
        key = match.group(1).strip()
        value = match.group(2).strip()
        result[key] = value
    return result


def emit(event_type: str, message: str, **extra: Any) -> None:
    payload = {"type": event_type, "message": message, **extra}
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def resolve_python_cmd(preferred: Optional[List[str]] = None) -> Optional[List[str]]:
    candidates: List[List[str]] = []
    if preferred:
        candidates.append(preferred)
    if URL_READER_VENV_PY.exists():
        candidates.append([str(URL_READER_VENV_PY)])
    candidates.append([sys.executable])
    candidates.append(["python"])
    if os.name == "nt":
        candidates.append(["py", "-3"])

    for cmd in candidates:
        try:
            cp = subprocess.run(
                cmd + ["--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=8,
            )
            if cp.returncode == 0:
                return cmd
        except Exception:
            continue
    return None


def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return None

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                return data
        except Exception:
            continue

    first = raw.find("{")
    last = raw.rfind("}")
    if first >= 0 and last > first:
        try:
            data = json.loads(raw[first:last + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            return None
    return None


def write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def ensure_context_placeholders() -> Dict[str, Path]:
    contexts = {
        "url": CRAWL_ROOT / "contexts" / "latest-url-crawl.md",
        "keyword": CRAWL_ROOT / "contexts" / "latest-keyword-matrix.md",
        "brief": CRAWL_ROOT / "contexts" / "latest-crawl-brief.md",
    }

    placeholder = (
        "# 抓取结果待生成\n\n"
        "当前文件尚未生成。请在桌面端进入“内容抓取台”执行一次抓取任务。\n"
    )

    for path in contexts.values():
        if not path.exists():
            write_markdown(path, placeholder)

    return contexts


def run_url_reader(
    python_cmd: List[str],
    url: str,
    output_dir: Path,
) -> Tuple[bool, Dict[str, Any], str]:
    inline = (
        "import json, sys, pathlib; "
        "root = pathlib.Path(sys.argv[1]); "
        "sys.path.insert(0, str(root / 'scripts')); "
        "import url_reader; "
        "res = url_reader.read_and_save(sys.argv[2], output_dir=sys.argv[3], verbose=False); "
        # Use ASCII-safe JSON to avoid Windows console encoding issues in subprocess stdout.
        "print(json.dumps(res, ensure_ascii=True))"
    )

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    cp = subprocess.run(
        python_cmd + ["-c", inline, str(URL_READER_ROOT), url, str(output_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    combined = "\n".join([cp.stdout or "", cp.stderr or ""]).strip()
    parsed = extract_json_from_text(cp.stdout or "")

    if cp.returncode != 0 or not parsed:
        return False, {"success": False, "errors": [combined or "URL Reader 执行失败"]}, combined

    ok = bool(parsed.get("success"))
    return ok, parsed, combined


def run_url_mode(
    payload: Dict[str, Any],
    run_id: str,
    artifacts: List[str],
) -> Dict[str, Any]:
    urls = payload.get("urls") or []
    urls = [item for item in urls if item]
    if not urls:
        return {"status": "skipped", "errors": ["URL 模式未提供有效 URL"]}

    py_cmd = resolve_python_cmd([str(URL_READER_VENV_PY)] if URL_READER_VENV_PY.exists() else None)
    if not py_cmd:
        return {"status": "error", "errors": ["未找到可用 Python，无法执行 URL 抓取"]}

    emit("log", "开始 URL 抓取", count=len(urls), python=" ".join(py_cmd))

    date_str = today()
    output_dir = CRAWL_ROOT / "url-reader-output" / run_id
    raw_dir = CRAWL_ROOT / "raw" / "url" / date_str
    report_dir = CRAWL_ROOT / "reports" / date_str
    normalized_dir = CRAWL_ROOT / "normalized" / date_str
    raw_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    csv_rows: List[Dict[str, Any]] = []

    for index, url in enumerate(urls, start=1):
        emit("log", f"URL 抓取中 ({index}/{len(urls)})", url=url)
        ok, result, combined = run_url_reader(py_cmd, url, output_dir)
        save_info = result.get("save") if isinstance(result, dict) else None
        md_file = save_info.get("md_file") if isinstance(save_info, dict) else ""
        title = save_info.get("title") if isinstance(save_info, dict) else ""
        strategy = result.get("strategy") if isinstance(result, dict) else ""
        errors = []
        if isinstance(result, dict):
            if isinstance(result.get("errors"), list):
                errors.extend([str(err) for err in result.get("errors") if str(err).strip()])
            if result.get("error"):
                errors.append(str(result.get("error")))
        if (not errors) and (not ok):
            errors.append(combined or "抓取失败")

        copied_md = ""
        if ok and md_file and Path(md_file).exists():
            copy_name = f"{index:02d}-{safe_name(title or url, 60)}.md"
            target = raw_dir / copy_name
            shutil.copyfile(md_file, target)
            copied_md = str(target)
            artifacts.append(str(target))

        row = {
            "url": url,
            "status": "success" if ok else "failed",
            "title": title or "",
            "strategy": strategy or "",
            "saved_md": copied_md or md_file or "",
            "errors": errors,
        }
        rows.append(row)
        csv_rows.append(
            {
                "platform": "url",
                "keyword": "",
                "post_id": "",
                "author_id": "",
                "title": row["title"],
                "content_snippet": "",
                "publish_time": "",
                "engagement_like": "",
                "engagement_comment": "",
                "engagement_share": "",
                "url": row["url"],
                "crawl_time": now_iso(),
            }
        )

    csv_path = normalized_dir / f"url-crawl-{run_id}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(csv_rows[0].keys()) if csv_rows else [
            "platform", "keyword", "post_id", "author_id", "title", "content_snippet",
            "publish_time", "engagement_like", "engagement_comment", "engagement_share", "url", "crawl_time"
        ])
        writer.writeheader()
        for row in csv_rows:
            writer.writerow(row)
    artifacts.append(str(csv_path))

    success_count = sum(1 for row in rows if row["status"] == "success")
    failed_count = len(rows) - success_count

    report_path = report_dir / f"url-crawl-summary-{run_id}.md"
    report_lines = [
        "# URL 抓取摘要",
        "",
        f"- 运行ID：{run_id}",
        f"- 成功：{success_count}",
        f"- 失败：{failed_count}",
        "",
        "## 明细",
    ]
    for row in rows:
        report_lines.append(f"- `{row['status']}` {row['url']}")
        if row["title"]:
            report_lines.append(f"  - 标题：{row['title']}")
        if row["strategy"]:
            report_lines.append(f"  - 策略：{row['strategy']}")
        if row["saved_md"]:
            report_lines.append(f"  - 文件：{row['saved_md']}")
        if row["errors"]:
            report_lines.append(f"  - 错误：{' | '.join(row['errors'])}")
    write_markdown(report_path, "\n".join(report_lines) + "\n")
    artifacts.append(str(report_path))

    contexts = ensure_context_placeholders()
    context_lines = [
        "# 最新 URL 抓取摘要",
        "",
        f"- 更新时间：{now_iso()}",
        f"- 运行ID：{run_id}",
        f"- 成功：{success_count}",
        f"- 失败：{failed_count}",
        "",
        "## 结果列表",
    ]
    for row in rows:
        context_lines.append(f"- `{row['status']}` {row['url']}")
        if row["title"]:
            context_lines.append(f"  - 标题：{row['title']}")
        if row["saved_md"]:
            context_lines.append(f"  - 摘要文件：{row['saved_md']}")
        if row["errors"]:
            context_lines.append(f"  - 错误：{' | '.join(row['errors'])}")
    write_markdown(contexts["url"], "\n".join(context_lines) + "\n")
    artifacts.append(str(contexts["url"]))

    status = "success" if failed_count == 0 else ("partial" if success_count > 0 else "error")
    errors = [f"{row['url']}: {' | '.join(row['errors'])}" for row in rows if row["errors"]]
    return {
        "status": status,
        "count": len(rows),
        "success": success_count,
        "failed": failed_count,
        "errors": errors,
        "report": str(report_path),
        "context": str(contexts["url"]),
    }


def run_keyword_mode(
    payload: Dict[str, Any],
    run_id: str,
    artifacts: List[str],
) -> Dict[str, Any]:
    keywords = payload.get("keywords") or []
    keywords = [item for item in keywords if item]
    if not keywords:
        return {"status": "skipped", "errors": ["关键词模式未提供有效关键词"]}

    platforms = payload.get("platforms") or ["wechat", "xhs", "x"]
    dry_run = bool(payload.get("dry_run"))

    script_path = INTERNAL_KEYWORD_SCRIPT if INTERNAL_KEYWORD_SCRIPT.exists() else EXTERNAL_KEYWORD_SCRIPT
    source = "internal" if script_path == INTERNAL_KEYWORD_SCRIPT else "external"

    if not script_path.exists():
        return {"status": "error", "errors": ["未找到关键词抓取脚本（内置与外部回退均不存在）"], "source": "missing"}

    py_cmd = resolve_python_cmd([str(URL_READER_VENV_PY)] if URL_READER_VENV_PY.exists() else None)
    if not py_cmd:
        return {"status": "error", "errors": ["未找到可用 Python，无法执行关键词抓取"], "source": source}

    emit("log", "开始关键词矩阵抓取", source=source, python=" ".join(py_cmd))

    request_payload: Dict[str, Any] = {
        "platforms": platforms,
        "keywords": keywords,
        "max_results": int(payload.get("max_results") or 30),
        "dedup_key": payload.get("dedup_key") or "url",
    }
    since = str(payload.get("since") or "").strip()
    until = str(payload.get("until") or "").strip()
    if since or until:
        request_payload["time_window"] = {}
        if since:
            request_payload["time_window"]["since"] = since
        if until:
            request_payload["time_window"]["until"] = until

    run_tmp = CRAWL_ROOT / "runs" / "_tmp"
    run_tmp.mkdir(parents=True, exist_ok=True)
    request_path = run_tmp / f"keyword-request-{run_id}.json"
    request_path.write_text(json.dumps(request_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    command = py_cmd + [str(script_path), "--input", str(request_path), "--workspace", str(CRAWL_ROOT)]
    if dry_run:
        command.append("--dry-run")

    cp = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    parsed = extract_json_from_text(cp.stdout or "")
    stderr_text = (cp.stderr or "").strip()

    if cp.returncode != 0 or not parsed:
        message = stderr_text or (cp.stdout or "").strip() or "关键词抓取执行失败"
        return {"status": "error", "errors": [message], "source": source}

    status = str(parsed.get("status") or "").strip().lower()
    if status in {"success", "dry_run"}:
        run_status = "success"
    else:
        run_status = "error"

    raw_files = [str(item) for item in (parsed.get("raw_files") or []) if str(item).strip()]
    for path in raw_files:
        artifacts.append(path)
    if parsed.get("normalized_csv"):
        artifacts.append(str(parsed.get("normalized_csv")))
    if parsed.get("summary_report"):
        artifacts.append(str(parsed.get("summary_report")))
    if parsed.get("run_log"):
        artifacts.append(str(parsed.get("run_log")))

    contexts = ensure_context_placeholders()
    context_lines = [
        "# 最新关键词矩阵摘要",
        "",
        f"- 更新时间：{now_iso()}",
        f"- 运行ID：{run_id}",
        f"- 来源：{source}",
        f"- Dry Run：{'是' if dry_run else '否'}",
        "",
        "## 产物",
        f"- Normalized CSV：{parsed.get('normalized_csv') or '无'}",
        f"- Summary Report：{parsed.get('summary_report') or '无'}",
        f"- Run Log：{parsed.get('run_log') or '无'}",
        "",
        "## 请求参数",
        f"- platforms：{', '.join(platforms)}",
        f"- keywords：{', '.join(keywords)}",
        f"- max_results：{request_payload.get('max_results')}",
        f"- dedup_key：{request_payload.get('dedup_key')}",
    ]
    if since or until:
        context_lines.append(f"- time_window：{since or '-'} ~ {until or '-'}")
    write_markdown(contexts["keyword"], "\n".join(context_lines) + "\n")
    artifacts.append(str(contexts["keyword"]))

    return {
        "status": run_status,
        "dry_run": dry_run,
        "source": source,
        "errors": [] if run_status == "success" else [stderr_text or "关键词抓取失败"],
        "context": str(contexts["keyword"]),
    }


def build_payload(raw_payload: Dict[str, Any]) -> Dict[str, Any]:
    brief = str(raw_payload.get("brief") or "")
    parsed_brief = parse_brief_lines(brief)

    mode_text = str(raw_payload.get("mode") or parsed_brief.get("模式") or parsed_brief.get("输入模式") or "URL抓取")
    normalized_mode = "url"
    if "双" in mode_text:
        normalized_mode = "dual"
    elif "关键词" in mode_text:
        normalized_mode = "keyword"

    urls = raw_payload.get("urls")
    if not isinstance(urls, list):
        urls = split_lines(raw_payload.get("urlList") or parsed_brief.get("URL列表") or "")
    keywords = raw_payload.get("keywords")
    if not isinstance(keywords, list):
        keywords = split_lines(raw_payload.get("keywordList") or parsed_brief.get("关键词列表") or "")
    platforms = raw_payload.get("platforms")
    if not isinstance(platforms, list):
        platforms = split_lines(raw_payload.get("platformList") or parsed_brief.get("平台列表") or "wechat,xhs,x")

    since = str(raw_payload.get("since") or parsed_brief.get("时间窗口起") or "").strip()
    until = str(raw_payload.get("until") or parsed_brief.get("时间窗口止") or "").strip()
    max_results = raw_payload.get("max_results") or parsed_brief.get("max_results") or 30
    dedup_key = str(raw_payload.get("dedup_key") or parsed_brief.get("dedup_key") or "url").strip().lower()
    if dedup_key not in {"url", "title"}:
        dedup_key = "url"
    dry_run = as_bool(raw_payload.get("dry_run") or parsed_brief.get("dry_run"))

    try:
        max_results = int(max_results)
    except Exception:
        max_results = 30
    if max_results <= 0:
        max_results = 30

    return {
        "mode": normalized_mode,
        "modeText": mode_text,
        "urls": [url for url in urls if url],
        "keywords": [kw for kw in keywords if kw],
        "platforms": [pf for pf in platforms if pf],
        "since": since,
        "until": until,
        "max_results": max_results,
        "dedup_key": dedup_key,
        "dry_run": dry_run,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Desktop crawl bridge")
    parser.add_argument("--payload-file", required=True, help="Path to payload JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload_path = Path(args.payload_file).resolve()
    if not payload_path.exists():
        emit("error", "payload 文件不存在", path=str(payload_path))
        print(json.dumps({"type": "result", "data": {"status": "error", "errors": ["payload 文件不存在"]}}, ensure_ascii=False))
        return 1

    raw_payload = json.loads(payload_path.read_text(encoding="utf-8-sig"))
    payload = build_payload(raw_payload)
    run_id = f"crawl_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    ensure_context_placeholders()

    artifacts: List[str] = []
    errors: List[str] = []
    partial_failures: List[Dict[str, Any]] = []
    mode = payload["mode"]

    emit("log", "抓取任务开始", run_id=run_id, mode=mode, modeText=payload["modeText"])

    url_result: Optional[Dict[str, Any]] = None
    keyword_result: Optional[Dict[str, Any]] = None

    if mode in {"url", "dual"}:
        url_result = run_url_mode(payload, run_id, artifacts)
        if url_result.get("status") in {"error", "partial"}:
            errors.extend(url_result.get("errors") or [])
            if url_result.get("status") != "error":
                partial_failures.append({"component": "url", "errors": url_result.get("errors") or []})
        elif url_result.get("status") == "skipped":
            errors.extend(url_result.get("errors") or [])

    if mode in {"keyword", "dual"}:
        keyword_result = run_keyword_mode(payload, run_id, artifacts)
        if keyword_result.get("status") != "success":
            errors.extend(keyword_result.get("errors") or [])
            partial_failures.append({"component": "keyword", "errors": keyword_result.get("errors") or []})

    contexts = ensure_context_placeholders()
    brief_lines = [
        "# 最新抓取任务总览",
        "",
        f"- 更新时间：{now_iso()}",
        f"- 运行ID：{run_id}",
        f"- 模式：{payload['modeText']}",
        f"- URL数量：{len(payload['urls'])}",
        f"- 关键词数量：{len(payload['keywords'])}",
        f"- 平台：{', '.join(payload['platforms']) if payload['platforms'] else '-'}",
        f"- 时间窗口：{payload['since'] or '-'} ~ {payload['until'] or '-'}",
        f"- max_results：{payload['max_results']}",
        f"- dedup_key：{payload['dedup_key']}",
        f"- dry_run：{'是' if payload['dry_run'] else '否'}",
        "",
        "## 子任务状态",
    ]
    if url_result is not None:
        brief_lines.append(f"- URL：{url_result.get('status', 'unknown')}")
    if keyword_result is not None:
        brief_lines.append(f"- 关键词矩阵：{keyword_result.get('status', 'unknown')}")
    if errors:
        brief_lines.append("")
        brief_lines.append("## 错误")
        for item in errors:
            brief_lines.append(f"- {item}")
    write_markdown(contexts["brief"], "\n".join(brief_lines) + "\n")
    artifacts.append(str(contexts["brief"]))

    run_status = "done"
    if mode == "dual":
        component_statuses = []
        if url_result is not None:
            component_statuses.append(url_result.get("status"))
        if keyword_result is not None:
            component_statuses.append(keyword_result.get("status"))
        success_count = sum(1 for s in component_statuses if s == "success")
        if success_count == len(component_statuses) and success_count > 0:
            run_status = "done"
        elif success_count > 0:
            run_status = "partial"
        else:
            run_status = "error"
    elif mode == "url":
        run_status = "done" if (url_result and url_result.get("status") == "success") else "error"
    elif mode == "keyword":
        run_status = "done" if (keyword_result and keyword_result.get("status") == "success") else "error"

    run_payload = {
        "run_id": run_id,
        "status": run_status,
        "mode": mode,
        "modeText": payload["modeText"],
        "input": payload,
        "artifacts": sorted(set(artifacts)),
        "contexts_latest": {
            "url": str(contexts["url"]),
            "keyword": str(contexts["keyword"]),
            "brief": str(contexts["brief"]),
        },
        "errors": errors,
        "partialFailures": partial_failures,
        "components": {
            "url": url_result,
            "keyword": keyword_result,
        },
    }

    run_file = CRAWL_ROOT / "runs" / f"{run_id}.json"
    run_file.parent.mkdir(parents=True, exist_ok=True)
    run_file.write_text(json.dumps(run_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    run_payload["run_file"] = str(run_file)

    emit("log", "抓取任务结束", run_id=run_id, status=run_status)
    print(json.dumps({"type": "result", "data": run_payload}, ensure_ascii=False), flush=True)
    return 0 if run_status in {"done", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

