#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily retro runner from normalized metrics."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from automation_state import (
    METRICS_DIR,
    REPO_ROOT,
    add_task_log,
    append_dead_letter,
    append_run_log,
    create_task,
    make_task_id,
    update_task,
)
from feishu_http_client import safe_sync_retro_summary


REPORT_DIR = REPO_ROOT / "04-数据与方法论" / "方法论沉淀" / "日报"
BRIEF_DIR = REPO_ROOT / "01-选题管理" / "次日创作输入"


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


def _load_metrics(date_str: str) -> list[dict[str, Any]]:
    metrics_file = METRICS_DIR / date_str / "metrics.json"
    if not metrics_file.exists():
        return []
    try:
        data = json.loads(metrics_file.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            out.append(item)
    return out


def _engagement_score(row: dict[str, Any]) -> float:
    read_or_play = int(row.get("views") or 0) or int(row.get("play") or 0)
    likes = int(row.get("likes") or 0)
    comments = int(row.get("comments") or 0)
    shares = int(row.get("shares") or 0)
    collects = int(row.get("collects") or 0)
    denominator = max(1, read_or_play)
    weighted = likes + comments * 2 + shares * 3 + collects * 2
    return float(weighted) / float(denominator)


def _grade(score: float) -> str:
    if score >= 0.15:
        return "S"
    if score >= 0.08:
        return "A"
    if score >= 0.03:
        return "B"
    return "C"


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored: list[dict[str, Any]] = []
    for row in rows:
        score = _engagement_score(row)
        grade = _grade(score)
        copy = dict(row)
        copy["score"] = score
        copy["grade"] = grade
        scored.append(copy)

    scored.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    top = scored[:3]
    tail = scored[-3:] if len(scored) > 3 else []

    grade_count = {"S": 0, "A": 0, "B": 0, "C": 0}
    for row in scored:
        g = str(row.get("grade") or "C")
        if g in grade_count:
            grade_count[g] += 1

    summary = {
        "total": len(scored),
        "grade_count": grade_count,
        "top": top,
        "tail": tail,
        "scored": scored,
    }
    return summary


def _build_actions(summary: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    grades = summary.get("grade_count") or {}
    total = int(summary.get("total") or 0)
    c_ratio = (int(grades.get("C") or 0) / total) if total else 0.0

    if c_ratio >= 0.5:
        actions.append("明天优先使用‘冲突开场 + 明确受众 + 单一承诺’的前3秒结构。")
    if int(grades.get("A") or 0) + int(grades.get("S") or 0) >= max(1, total // 3):
        actions.append("复用A/S内容的标题句式和封面关键词，保持同主题连发2-3条。")
    if not actions:
        actions.append("保持当前选题方向，重点优化发布时间与首图点击率。")

    actions.append("每条内容固定补充1个可执行动作句，减少泛观点表达。")
    actions.append("抖音内容继续保留人工最终点击确认，并记录失败原因标签。")
    return actions


def _render_row_line(row: dict[str, Any]) -> str:
    return "- [{grade}] {platform}/{account} task={task_id} score={score:.3f} read_or_play={rop} likes={likes} comments={comments} shares={shares} collects={collects}".format(
        grade=str(row.get("grade") or "C"),
        platform=str(row.get("platform") or "-"),
        account=str(row.get("account") or "-"),
        task_id=str(row.get("task_id") or "-")[:28],
        score=float(row.get("score") or 0.0),
        rop=int(row.get("views") or 0) or int(row.get("play") or 0),
        likes=int(row.get("likes") or 0),
        comments=int(row.get("comments") or 0),
        shares=int(row.get("shares") or 0),
        collects=int(row.get("collects") or 0),
    )


def _write_report(date_str: str, summary: dict[str, Any], actions: list[str]) -> str:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{date_str}.md"

    top_lines = [_render_row_line(item) for item in (summary.get("top") or [])]
    tail_lines = [_render_row_line(item) for item in (summary.get("tail") or [])]

    content = [
        f"# {date_str} 内容复盘日报",
        "",
        "## 总览",
        f"- 样本数: {int(summary.get('total') or 0)}",
        "- 分级: S={S} A={A} B={B} C={C}".format(
            S=int((summary.get("grade_count") or {}).get("S") or 0),
            A=int((summary.get("grade_count") or {}).get("A") or 0),
            B=int((summary.get("grade_count") or {}).get("B") or 0),
            C=int((summary.get("grade_count") or {}).get("C") or 0),
        ),
        "",
        "## 亮点",
    ]
    content.extend(top_lines or ["- 无有效亮点样本。"])
    content.append("")
    content.append("## 问题")
    content.extend(tail_lines or ["- 无明显低分样本。"])
    content.append("")
    content.append("## 明日优化动作")
    content.extend([f"- {item}" for item in actions])

    path.write_text("\n".join(content).rstrip() + "\n", encoding="utf-8")
    return path.relative_to(REPO_ROOT).as_posix()


def _write_nextday_brief(date_str: str, summary: dict[str, Any], actions: list[str]) -> str:
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    path = BRIEF_DIR / f"{date_str}-建议Brief.md"

    top = summary.get("top") or []
    key_topics = [str(item.get("platform") or "") for item in top if str(item.get("platform") or "")]
    topic_line = "、".join(list(dict.fromkeys(key_topics))[:3]) or "公众号/小红书/抖音"

    content = [
        f"# {date_str} 次日创作输入",
        "",
        "## 优先方向",
        f"- 重点平台: {topic_line}",
        "- 目标: 提升首屏留存与互动密度，减少低互动长段落。",
        "",
        "## 选题约束",
        "- 每条内容只解决一个核心问题。",
        "- 标题必须包含受众 + 利益点 + 场景。",
        "- 正文第1屏给出结论，随后给证据与动作。",
        "",
        "## 执行动作",
    ]
    content.extend([f"- {item}" for item in actions])

    path.write_text("\n".join(content).rstrip() + "\n", encoding="utf-8")
    return path.relative_to(REPO_ROOT).as_posix()


def run_retro(payload: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    date_str = _normalize_date(str(payload.get("date") or payload.get("日期") or "today"))
    event_ref = str(payload.get("event_ref") or "").strip()
    source_user = str(payload.get("source_user") or "").strip()

    task_id = make_task_id("retro")
    create_task(
        task_id=task_id,
        event_ref=event_ref,
        task_type="retro_run",
        status="running",
        phase="analyze",
        platform="all",
        source_user=source_user,
        payload={"request": payload, "date": date_str},
        result={"status": "running"},
    )

    try:
        rows = _load_metrics(date_str)
        if not rows:
            raise RuntimeError(f"metrics file missing or empty for {date_str}")

        summary = _summarize(rows)
        actions = _build_actions(summary)

        if dry_run:
            result = {"status": "dry_run", "date": date_str, "summary": summary, "actions": actions}
            update_task(task_id, status="success", phase="analyze", result_json=_json_dumps(result), error_text="")
            return {"status": "success", "task_id": task_id, "dry_run": True, "result": result}

        report_rel = _write_report(date_str, summary, actions)
        brief_rel = _write_nextday_brief(date_str, summary, actions)
        doc_sync = safe_sync_retro_summary(
            f"{date_str} 自动复盘\n" + "\n".join([f"- {item}" for item in actions])
        )

        result = {
            "status": "success",
            "date": date_str,
            "summary": summary,
            "actions": actions,
            "report_path": report_rel,
            "brief_path": brief_rel,
            "feishu_doc_sync": doc_sync,
        }

        update_task(task_id, status="success", phase="completed", result_json=_json_dumps(result), error_text="")
        add_task_log(task_id, "retro_completed", {"date": date_str, "report": report_rel, "brief": brief_rel})
        run_log = append_run_log("retro_run", {"task_id": task_id, "date": date_str, "report": report_rel, "brief": brief_rel})

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
        update_task(task_id, status="error", phase="analyze", error_text=error_text)
        add_task_log(task_id, "retro_failed", {"error": error_text})
        dead_log = append_dead_letter("retro_run_failed", {"task_id": task_id, "date": date_str, "error": error_text})
        return {
            "status": "error",
            "task_id": task_id,
            "phase": "analyze",
            "date": date_str,
            "errors": [error_text],
            "dead_letter_log": dead_log,
        }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retro runner")
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
    result = run_retro(payload, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if str(result.get("status") or "") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main(os.sys.argv[1:]))
