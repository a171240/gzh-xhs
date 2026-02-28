#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Media generation runner based on VideoFly APIs."""

from __future__ import annotations

import argparse
import dataclasses
import json
import mimetypes
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import requests

from automation_state import (
    MEDIA_INBOX_DIR,
    REPO_ROOT,
    add_task_log,
    append_dead_letter,
    append_run_log,
    create_task,
    find_task_by_event_ref,
    get_task,
    make_task_id,
    update_task,
)


NETWORK_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi"}


@dataclasses.dataclass(frozen=True)
class MediaRunnerSettings:
    videofly_base_url: str
    timeout_sec: int
    verify_ssl: bool
    network_retry: int
    backoff_base_sec: float
    poll_interval_sec: int
    poll_timeout_sec: int
    max_download_mb: int
    default_model: str


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_platform(value: str) -> str:
    raw = str(value or "").strip().lower()
    return {
        "wechat": "wechat",
        "公众号": "wechat",
        "xhs": "xhs",
        "小红书": "xhs",
        "douyin": "douyin",
        "抖音": "douyin",
    }.get(raw, raw)


def _normalize_mode(value: str) -> str:
    raw = str(value or "").strip().lower()
    return {
        "text": "text",
        "text-to-video": "text",
        "image": "image",
        "image-to-video": "image",
        "reference-video": "reference-video",
        "reference_to_video": "reference-video",
        "video": "reference-video",
        "video-to-video": "reference-video",
    }.get(raw, raw or "text")


def _load_settings() -> MediaRunnerSettings:
    return MediaRunnerSettings(
        videofly_base_url=str(os.getenv("VIDEOFLY_BASE_URL") or "http://127.0.0.1:3000").strip().rstrip("/"),
        timeout_sec=max(8, int(os.getenv("VIDEOFLY_TIMEOUT_SEC", "45"))),
        verify_ssl=_as_bool(os.getenv("VIDEOFLY_VERIFY_SSL"), default=True),
        network_retry=max(1, int(os.getenv("AUTOMATION_NETWORK_RETRY", "3"))),
        backoff_base_sec=max(0.2, float(os.getenv("AUTOMATION_BACKOFF_BASE_SEC", "1.0"))),
        poll_interval_sec=max(2, int(os.getenv("VIDEOFLY_POLL_INTERVAL_SEC", "8"))),
        poll_timeout_sec=max(30, int(os.getenv("VIDEOFLY_POLL_TIMEOUT_SEC", "900"))),
        max_download_mb=max(10, int(os.getenv("MEDIA_DOWNLOAD_MAX_MB", "500"))),
        default_model=str(os.getenv("VIDEOFLY_DEFAULT_MODEL") or "wan2.6").strip() or "wan2.6",
    )


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _headers_json() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = str(os.getenv("VIDEOFLY_BEARER_TOKEN") or "").strip()
    cookie = str(os.getenv("VIDEOFLY_COOKIE") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _headers_plain() -> dict[str, str]:
    headers: dict[str, str] = {}
    token = str(os.getenv("VIDEOFLY_BEARER_TOKEN") or "").strip()
    cookie = str(os.getenv("VIDEOFLY_COOKIE") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _request_json(
    *,
    settings: MediaRunnerSettings,
    method: str,
    url: str,
    json_body: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attempts = settings.network_retry
    for idx in range(1, attempts + 1):
        try:
            if files:
                response = requests.request(
                    method,
                    url,
                    data=json_body,
                    files=files,
                    headers=_headers_plain(),
                    timeout=settings.timeout_sec,
                    verify=settings.verify_ssl,
                )
            else:
                response = requests.request(
                    method,
                    url,
                    json=json_body,
                    headers=_headers_json(),
                    timeout=settings.timeout_sec,
                    verify=settings.verify_ssl,
                )
        except requests.RequestException as exc:
            if idx >= attempts:
                raise RuntimeError(f"network error: {exc}") from exc
            time.sleep(settings.backoff_base_sec * (2 ** (idx - 1)))
            continue

        status = int(response.status_code)
        if status >= 400:
            body_text = response.text[:2000]
            if status in NETWORK_RETRYABLE_STATUS and idx < attempts:
                time.sleep(settings.backoff_base_sec * (2 ** (idx - 1)))
                continue
            raise RuntimeError(f"http {status}: {body_text}")

        try:
            return response.json()
        except Exception as exc:
            raise RuntimeError(f"invalid json response: {exc}") from exc

    raise RuntimeError("unexpected request loop break")


def _unwrap_data(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("data"), dict):
        return dict(payload["data"])
    return dict(payload)


def _sanitize_filename(name: str, fallback: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", "-", str(name or "").strip())
    value = re.sub(r"\s+", "-", value).strip("-")
    return value or fallback


def _guess_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    mime, _ = mimetypes.guess_type(str(path))
    if mime and mime.startswith("video/"):
        return "video"
    return "image"


def _download_to_path(
    *,
    settings: MediaRunnerSettings,
    source_url: str,
    target_path: Path,
) -> Path:
    max_bytes = settings.max_download_mb * 1024 * 1024
    with requests.get(
        source_url,
        headers=_headers_plain(),
        timeout=settings.timeout_sec,
        verify=settings.verify_ssl,
        stream=True,
    ) as resp:
        if resp.status_code >= 400:
            raise RuntimeError(f"download failed {resp.status_code}: {source_url}")
        size = 0
        with target_path.open("wb") as fw:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise RuntimeError(f"file exceeds {settings.max_download_mb}MB: {source_url}")
                fw.write(chunk)
    return target_path


def _collect_source_assets(payload: dict[str, Any]) -> list[dict[str, str]]:
    assets: list[dict[str, str]] = []

    def push(url: str, kind: str = "") -> None:
        value = str(url or "").strip()
        if not value:
            return
        assets.append({"url": value, "kind": kind})

    for key in ("urls", "image_urls", "video_urls"):
        raw = payload.get(key)
        if isinstance(raw, list):
            kind = "video" if "video" in key else "image"
            for item in raw:
                push(str(item), kind)

    attachments = payload.get("attachments")
    if isinstance(attachments, list):
        for item in attachments:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or item.get("download_url") or item.get("file_url") or "").strip()
            mime = str(item.get("mime") or item.get("content_type") or "").lower()
            kind = "video" if mime.startswith("video/") else ""
            push(url, kind)

    for key in ("image", "image_url", "video", "video_url", "reference_video"):
        value = str(payload.get(key) or "").strip()
        if not value:
            continue
        kind = "video" if "video" in key else "image"
        push(value, kind)

    dedup: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in assets:
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        dedup.append(item)
    return dedup


def _materialize_assets(
    *,
    settings: MediaRunnerSettings,
    payload: dict[str, Any],
    task_dir: Path,
) -> dict[str, list[str]]:
    _ensure_dir(task_dir)
    images: list[str] = []
    videos: list[str] = []

    for idx, item in enumerate(_collect_source_assets(payload), start=1):
        source = str(item.get("url") or "").strip()
        hint_kind = str(item.get("kind") or "").strip().lower()
        if not source:
            continue

        # Local file path.
        local_candidate = Path(source)
        if local_candidate.exists() and local_candidate.is_file():
            name = _sanitize_filename(local_candidate.name, f"asset-{idx}{local_candidate.suffix or '.bin'}")
            target = task_dir / name
            if local_candidate.resolve() != target.resolve():
                shutil.copy2(local_candidate, target)
            kind = hint_kind or _guess_kind(target)
            if kind == "video":
                videos.append(str(target))
            else:
                images.append(str(target))
            continue

        # Remote URL download.
        parsed_name = source.split("?")[0].split("#")[0].rstrip("/").split("/")[-1]
        suffix = Path(parsed_name).suffix or (".mp4" if hint_kind == "video" else ".jpg")
        filename = _sanitize_filename(parsed_name or f"asset-{idx}{suffix}", f"asset-{idx}{suffix}")
        target = task_dir / filename
        _download_to_path(settings=settings, source_url=source, target_path=target)

        kind = hint_kind or _guess_kind(target)
        if kind == "video":
            videos.append(str(target))
        else:
            images.append(str(target))

    # Manual payload paths.
    for key in ("images", "videos"):
        raw = payload.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            value = str(item or "").strip()
            if not value:
                continue
            if value.startswith("http://") or value.startswith("https://"):
                if key == "images":
                    images.append(value)
                else:
                    videos.append(value)
                continue
            candidate = Path(value)
            if not candidate.exists() or not candidate.is_file():
                continue
            name = _sanitize_filename(candidate.name, candidate.name)
            target = task_dir / name
            if candidate.resolve() != target.resolve():
                shutil.copy2(candidate, target)
            if key == "images":
                images.append(str(target))
            else:
                videos.append(str(target))

    return {
        "images": list(dict.fromkeys(images)),
        "videos": list(dict.fromkeys(videos)),
    }


def _upload_media(
    *,
    settings: MediaRunnerSettings,
    local_path: Path,
) -> str:
    mime = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
    with local_path.open("rb") as fr:
        data = _request_json(
            settings=settings,
            method="POST",
            url=f"{settings.videofly_base_url}/api/v1/upload",
            json_body=None,
            files={"file": (local_path.name, fr, mime)},
        )
    payload = _unwrap_data(data)
    url = str(payload.get("publicUrl") or payload.get("url") or "").strip()
    if not url:
        raise RuntimeError(f"upload response missing publicUrl: {data}")
    return url


def _normalize_media_urls(settings: MediaRunnerSettings, values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        value = str(item or "").strip()
        if not value:
            continue
        if value.startswith("http://") or value.startswith("https://"):
            url = value
        else:
            path = Path(value)
            if not path.exists() or not path.is_file():
                continue
            url = _upload_media(settings=settings, local_path=path)
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _poll_generation(
    *,
    settings: MediaRunnerSettings,
    task_id: str,
    video_uuid: str,
) -> dict[str, Any]:
    started = time.time()
    last_payload: dict[str, Any] = {}

    while True:
        elapsed = int(time.time() - started)
        if elapsed > settings.poll_timeout_sec:
            raise RuntimeError("video generation polling timeout")

        if task_id:
            endpoint = f"{settings.videofly_base_url}/api/v1/video/task/{task_id}/status"
        elif video_uuid:
            endpoint = f"{settings.videofly_base_url}/api/v1/video/{video_uuid}/status"
        else:
            raise RuntimeError("missing task id and video uuid for polling")

        raw = _request_json(settings=settings, method="GET", url=endpoint)
        payload = _unwrap_data(raw)
        last_payload = payload

        status = str(payload.get("status") or "").strip().lower()
        if status in {"completed", "success"}:
            return payload
        if status in {"failed", "error", "canceled", "cancelled"}:
            raise RuntimeError(str(payload.get("error") or payload.get("message") or "video generation failed"))

        time.sleep(settings.poll_interval_sec)


def run_media_generate(payload: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    settings = _load_settings()

    event_ref = str(payload.get("event_ref") or "").strip()
    source_user = str(payload.get("source_user") or "").strip()
    platform = _normalize_platform(str(payload.get("platform") or ""))
    model = str(payload.get("model") or settings.default_model).strip() or settings.default_model
    mode = _normalize_mode(str(payload.get("mode") or "text"))
    prompt = str(payload.get("copy") or payload.get("prompt") or payload.get("文案") or "").strip()

    if not prompt:
        raise ValueError("prompt/copy is required")

    duplicate = find_task_by_event_ref(event_ref, "media_generate") if event_ref else None
    if duplicate:
        return {
            "status": "duplicate",
            "task_id": duplicate["task_id"],
            "event_ref": event_ref,
            "platform": duplicate.get("platform") or platform,
            "mode": duplicate.get("mode") or mode,
            "task": duplicate,
        }

    task_id = make_task_id("media")
    task_dir = MEDIA_INBOX_DIR / task_id
    assets = _materialize_assets(settings=settings, payload=payload, task_dir=task_dir)

    image_urls = _normalize_media_urls(settings, assets["images"])
    video_urls = _normalize_media_urls(settings, assets["videos"])

    if mode == "image" and not image_urls:
        raise ValueError("mode=image requires at least one image")
    if mode == "reference-video" and not video_urls:
        raise ValueError("mode=reference-video requires at least one video")

    generate_payload: dict[str, Any] = {
        "prompt": prompt,
        "model": model,
        "mode": mode,
        "outputNumber": int(payload.get("outputNumber") or payload.get("output_number") or 1),
    }
    if image_urls:
        generate_payload["imageUrls"] = image_urls
    if video_urls:
        generate_payload["videoUrls"] = video_urls

    for key in ("duration", "aspectRatio", "quality", "generateAudio"):
        if key in payload and payload.get(key) is not None:
            generate_payload[key] = payload.get(key)

    create_task(
        task_id=task_id,
        event_ref=event_ref,
        task_type="media_generate",
        status="running",
        phase="generate",
        platform=platform,
        mode=mode,
        source_user=source_user,
        payload={
            "request": payload,
            "assets": assets,
            "generate_payload": generate_payload,
            "task_dir": task_dir.relative_to(REPO_ROOT).as_posix(),
        },
        result={"status": "running"},
    )
    add_task_log(task_id, "media_generate_started", {"generate_payload": generate_payload})

    if dry_run:
        update_task(
            task_id,
            status="pending",
            phase="generate",
            result_json=_json_dumps({"status": "dry_run", "generate_payload": generate_payload}),
            error_text="",
        )
        return {
            "status": "success",
            "task_id": task_id,
            "phase": "generate",
            "dry_run": True,
            "generate_payload": generate_payload,
        }

    try:
        create_resp = _request_json(
            settings=settings,
            method="POST",
            url=f"{settings.videofly_base_url}/api/v1/video/generate",
            json_body=generate_payload,
        )
        create_data = _unwrap_data(create_resp)
        video_task_id = str(create_data.get("taskId") or create_data.get("task_id") or "").strip()
        video_uuid = str(create_data.get("videoUuid") or create_data.get("video_uuid") or "").strip()

        polled = _poll_generation(settings=settings, task_id=video_task_id, video_uuid=video_uuid)

        result = {
            "status": "success",
            "create": create_data,
            "poll": polled,
            "task_id": task_id,
            "video_task_id": video_task_id,
            "video_uuid": video_uuid,
            "video_url": str(polled.get("videoUrl") or polled.get("video_url") or ""),
        }

        update_task(
            task_id,
            status="success",
            phase="completed",
            result_json=_json_dumps(result),
            error_text="",
        )
        add_task_log(task_id, "media_generate_completed", result)
        run_log = append_run_log(
            "media_generate",
            {
                "task_id": task_id,
                "event_ref": event_ref,
                "platform": platform,
                "mode": mode,
                "video_task_id": video_task_id,
                "video_uuid": video_uuid,
                "status": "success",
            },
        )
        return {
            "status": "success",
            "task_id": task_id,
            "phase": "completed",
            "platform": platform,
            "mode": mode,
            "video_task_id": video_task_id,
            "video_uuid": video_uuid,
            "video_url": result["video_url"],
            "run_log": run_log,
            "result": result,
        }

    except Exception as exc:
        error_text = str(exc)
        update_task(
            task_id,
            status="error",
            phase="generate",
            error_text=error_text,
            result_json=_json_dumps({"status": "error", "error": error_text}),
        )
        add_task_log(task_id, "media_generate_failed", {"error": error_text})
        dead_log = append_dead_letter(
            "media_generate_failed",
            {
                "task_id": task_id,
                "event_ref": event_ref,
                "platform": platform,
                "mode": mode,
                "error": error_text,
            },
        )
        return {
            "status": "error",
            "task_id": task_id,
            "phase": "generate",
            "platform": platform,
            "mode": mode,
            "errors": [error_text],
            "dead_letter_log": dead_log,
        }


def run_media_task(payload: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    action = str(payload.get("action") or "generate").strip().lower()
    if action != "generate":
        raise ValueError(f"unsupported media action: {action}")
    return run_media_generate(payload, dry_run=dry_run)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Media task runner")
    parser.add_argument("--payload-file", default="", help="Path to payload JSON")
    parser.add_argument("--payload-json", default="", help="Inline payload JSON")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.payload_json:
        data = json.loads(args.payload_json)
        if not isinstance(data, dict):
            raise ValueError("payload-json must be an object")
        return data
    if args.payload_file:
        data = json.loads(Path(args.payload_file).read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("payload-file content must be an object")
        return data
    raise ValueError("payload-file or payload-json is required")


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    payload = _load_payload(args)
    try:
        result = run_media_task(payload, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if str(result.get("status") or "") in {"success", "duplicate"} else 1
    except Exception as exc:
        print(json.dumps({"status": "error", "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(os.sys.argv[1:]))
