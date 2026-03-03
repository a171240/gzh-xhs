#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Douyin ASR extractor used by ingest pipeline.

This module follows the local README flow:
1) parse share link -> video id + desc
2) download video + extract audio
3) send audio to ASR endpoint and return transcript
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
DEFAULT_ASR_API = "https://api.siliconflow.cn/v1/audio/transcriptions"
DEFAULT_ASR_MODEL = "FunAudioLLM/SenseVoiceSmall"
URL_RE = re.compile(r"https?://[^\s\u3000<>\"']+", re.IGNORECASE)
VIDEO_ID_PATTERNS = (
    re.compile(r"/(?:video|share/video)/(\d{8,32})(?:\D|$)", re.IGNORECASE),
    re.compile(r"\bvideo_id=(\d{8,32})\b", re.IGNORECASE),
)


class DouyinAsrError(RuntimeError):
    """Raised when ASR extraction fails."""


@dataclass(frozen=True)
class DouyinAsrResult:
    source_url: str
    resolved_url: str
    canonical_url: str
    share_page_url: str
    video_id: str
    title: str
    desc: str
    video_url: str
    transcript: str


def _pick_share_url(text_or_url: str) -> str:
    raw = str(text_or_url or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("http"):
        return raw
    urls = URL_RE.findall(raw)
    if not urls:
        return ""
    for candidate in urls:
        host = str(urlparse(candidate).netloc or "").lower()
        if "douyin.com" in host or "iesdouyin.com" in host:
            return candidate
    return urls[0]


def _extract_video_id(value: str) -> str:
    text = str(value or "")
    for pattern in VIDEO_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return str(match.group(1) or "").strip()
    parsed = urlparse(text)
    if parsed.query:
        qs = parse_qs(parsed.query)
        for key in ("video_id", "aweme_id", "item_id"):
            values = qs.get(key) or []
            if values:
                candidate = str(values[0] or "").strip()
                if re.fullmatch(r"\d{8,32}", candidate):
                    return candidate
    return ""


def _resolve_share_url(share_url: str, timeout_sec: int) -> str:
    headers = {"User-Agent": MOBILE_UA}
    raw = str(share_url or "").strip()
    if not raw:
        return ""

    try:
        first = requests.get(raw, headers=headers, timeout=timeout_sec, allow_redirects=False)
        location = str(first.headers.get("location") or "").strip()
        if location:
            first_hop = str(urljoin(raw, location) or "").strip()
            if _extract_video_id(first_hop):
                return first_hop
    except Exception:
        pass

    try:
        final = requests.get(raw, headers=headers, timeout=timeout_sec, allow_redirects=True)
        return str(final.url or "").strip() or raw
    except Exception:
        return raw


def _extract_router_data(html: str) -> dict[str, Any]:
    text = str(html or "")
    if not text:
        raise DouyinAsrError("empty_share_page")

    match = re.search(r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", text, flags=re.DOTALL)
    if match and match.group(1):
        snippet = str(match.group(1)).strip().rstrip(";")
        try:
            data = json.loads(snippet)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    render = re.search(r'<script[^>]*id="RENDER_DATA"[^>]*>(.*?)</script>', text, flags=re.DOTALL)
    if render and render.group(1):
        snippet = str(render.group(1)).strip()
        for candidate in (snippet, requests.utils.unquote(snippet)):
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except Exception:
                continue

    raise DouyinAsrError("router_data_parse_failed")


def _parse_video_info(router_data: dict[str, Any], video_id_hint: str) -> tuple[str, str, str]:
    loader = router_data.get("loaderData") if isinstance(router_data, dict) else None
    if not isinstance(loader, dict):
        raise DouyinAsrError("router_loader_missing")

    info_res = None
    for key in ("video_(id)/page", "note_(id)/page"):
        node = loader.get(key)
        if isinstance(node, dict) and isinstance(node.get("videoInfoRes"), dict):
            info_res = node.get("videoInfoRes")
            break
    if not isinstance(info_res, dict):
        for node in loader.values():
            if isinstance(node, dict) and isinstance(node.get("videoInfoRes"), dict):
                info_res = node.get("videoInfoRes")
                break

    items = info_res.get("item_list") if isinstance(info_res, dict) else None
    if not isinstance(items, list) or not items:
        raise DouyinAsrError("video_item_missing")

    item = items[0] if isinstance(items[0], dict) else {}
    desc = str(item.get("desc") or "").strip()

    video = item.get("video") if isinstance(item, dict) else None
    play_addr = video.get("play_addr") if isinstance(video, dict) else None
    url_list = play_addr.get("url_list") if isinstance(play_addr, dict) else None
    video_url = str(url_list[0] or "").strip() if isinstance(url_list, list) and url_list else ""
    if not video_url:
        raise DouyinAsrError("video_url_missing")
    video_url = video_url.replace("playwm", "play")

    item_id = str(item.get("aweme_id") or item.get("id") or "").strip()
    final_video_id = item_id or str(video_id_hint or "").strip()
    if not final_video_id:
        raise DouyinAsrError("video_id_missing")

    title = desc or f"douyin_{final_video_id}"
    return final_video_id, title, video_url


def _download_binary(url: str, output_path: Path, timeout_sec: int) -> None:
    headers = {"User-Agent": MOBILE_UA}
    with requests.get(url, headers=headers, timeout=timeout_sec, stream=True, allow_redirects=True) as resp:
        resp.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb") as fw:
            for chunk in resp.iter_content(chunk_size=1024 * 128):
                if chunk:
                    fw.write(chunk)


def _ffmpeg_cmd() -> str:
    return str(os.getenv("FFMPEG_BIN") or "ffmpeg")


def _ffprobe_cmd() -> str:
    return str(os.getenv("FFPROBE_BIN") or "ffprobe")


def _extract_audio(video_path: Path, audio_path: Path) -> None:
    cmd = [
        _ffmpeg_cmd(),
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-q:a",
        "2",
        str(audio_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise DouyinAsrError(f"ffmpeg_extract_audio_failed:{(proc.stderr or proc.stdout or '').strip()[:300]}")


def _audio_duration_seconds(audio_path: Path) -> float:
    cmd = [
        _ffprobe_cmd(),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nokey=1:noprint_wrappers=1",
        str(audio_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return 0.0
    try:
        return float(str(proc.stdout or "").strip() or 0.0)
    except Exception:
        return 0.0


def _split_audio(audio_path: Path, segment_sec: int = 540) -> list[Path]:
    duration = _audio_duration_seconds(audio_path)
    if duration <= 0 or duration <= float(segment_sec):
        return [audio_path]

    pieces: list[Path] = []
    cursor = 0
    idx = 0
    while cursor < duration:
        part = audio_path.with_name(f"{audio_path.stem}.part{idx:03d}.mp3")
        cmd = [
            _ffmpeg_cmd(),
            "-y",
            "-ss",
            str(int(cursor)),
            "-t",
            str(int(segment_sec)),
            "-i",
            str(audio_path),
            "-acodec",
            "libmp3lame",
            "-q:a",
            "2",
            str(part),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise DouyinAsrError(f"ffmpeg_split_failed:{(proc.stderr or proc.stdout or '').strip()[:300]}")
        pieces.append(part)
        cursor += segment_sec
        idx += 1
    return pieces


def _asr_transcribe(audio_path: Path, api_key: str, timeout_sec: int) -> str:
    model = str(os.getenv("INGEST_DOUYIN_ASR_MODEL") or DEFAULT_ASR_MODEL).strip() or DEFAULT_ASR_MODEL
    endpoint = str(os.getenv("INGEST_DOUYIN_ASR_ENDPOINT") or DEFAULT_ASR_API).strip() or DEFAULT_ASR_API

    with audio_path.open("rb") as fh:
        files = {
            "file": (audio_path.name, fh, "audio/mpeg"),
            "model": (None, model),
        }
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.post(endpoint, files=files, headers=headers, timeout=max(30, int(timeout_sec)))
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    text = str((data or {}).get("text") or "").strip()
    if not text:
        raise DouyinAsrError("asr_empty_text")
    return text


def _cleanup(paths: list[Path]) -> None:
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            continue


def extract_douyin_asr_text(share_text_or_url: str, *, api_key: str = "", timeout_sec: int = 600) -> DouyinAsrResult:
    share_url = _pick_share_url(share_text_or_url)
    if not share_url:
        raise DouyinAsrError("share_url_missing")

    resolved = _resolve_share_url(share_url, timeout_sec=min(20, max(8, int(timeout_sec))))
    video_id = _extract_video_id(resolved) or _extract_video_id(share_url)
    if not video_id:
        raise DouyinAsrError("video_id_parse_failed")

    share_page_url = f"https://www.iesdouyin.com/share/video/{video_id}"
    headers = {"User-Agent": MOBILE_UA}
    page = requests.get(share_page_url, headers=headers, timeout=min(20, max(8, int(timeout_sec))))
    page.raise_for_status()

    router_data = _extract_router_data(page.text)
    final_video_id, title, video_url = _parse_video_info(router_data, video_id)
    canonical_url = f"https://www.douyin.com/video/{final_video_id}"
    desc = str(title or "").strip()

    key = str(api_key or os.getenv("INGEST_DOUYIN_ASR_API_KEY") or os.getenv("API_KEY") or "").strip()
    if not key:
        raise DouyinAsrError("asr_api_key_missing")

    with tempfile.TemporaryDirectory(prefix="douyin_asr_") as td:
        tmp = Path(td)
        video_path = tmp / f"{final_video_id}.mp4"
        audio_path = tmp / f"{final_video_id}.mp3"
        _download_binary(video_url, video_path, timeout_sec=min(60, max(15, int(timeout_sec))))
        _extract_audio(video_path, audio_path)

        parts = _split_audio(audio_path, segment_sec=max(120, int(os.getenv("INGEST_DOUYIN_ASR_SEGMENT_SEC", "540"))))
        text_parts: list[str] = []
        created_parts: list[Path] = []
        try:
            for part in parts:
                text_parts.append(_asr_transcribe(part, key, timeout_sec=int(timeout_sec)))
                if part != audio_path:
                    created_parts.append(part)
        finally:
            _cleanup(created_parts)

    transcript = "\n".join(part.strip() for part in text_parts if part and part.strip()).strip()
    if not transcript:
        raise DouyinAsrError("asr_transcript_empty")

    return DouyinAsrResult(
        source_url=share_url,
        resolved_url=resolved,
        canonical_url=canonical_url,
        share_page_url=share_page_url,
        video_id=final_video_id,
        title=desc,
        desc=desc,
        video_url=video_url,
        transcript=transcript,
    )


def extract_douyin_asr_dict(share_text_or_url: str, *, api_key: str = "", timeout_sec: int = 600) -> dict[str, Any]:
    result = extract_douyin_asr_text(share_text_or_url, api_key=api_key, timeout_sec=timeout_sec)
    return {
        "source_url": result.source_url,
        "resolved_url": result.resolved_url,
        "canonical_url": result.canonical_url,
        "share_page_url": result.share_page_url,
        "video_id": result.video_id,
        "title": result.title,
        "desc": result.desc,
        "video_url": result.video_url,
        "transcript": result.transcript,
        "source": "asr",
    }


__all__ = [
    "DouyinAsrError",
    "DouyinAsrResult",
    "extract_douyin_asr_text",
    "extract_douyin_asr_dict",
]
