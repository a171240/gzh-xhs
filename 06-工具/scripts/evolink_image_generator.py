from __future__ import annotations

import asyncio
import math
import os
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

ENV_FALLBACK_FILES = (
    ".env.ingest-writer.local",
    ".env.ingest-writer",
    ".env.feishu",
)


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _load_env_fallbacks() -> None:
    script_dir = Path(__file__).resolve().parent
    for name in ENV_FALLBACK_FILES:
        env_path = script_dir / name
        if not env_path.exists():
            continue
        text = env_path.read_text(encoding="utf-8", errors="ignore")
        for raw_line in text.splitlines():
            line = raw_line.strip().lstrip("\ufeff")
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and not (os.getenv(key) or "").strip():
                os.environ[key] = value


def _normalize_quality(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "2K"
    alias_map = {
        "low": "0.5K",
        "medium": "1K",
        "high": "2K",
        "ultra": "4K",
    }
    return alias_map.get(text.lower(), text)


def _normalize_size(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "auto"
    if ":" in text:
        return text
    lowered = text.lower()
    if lowered == "auto":
        return "auto"
    if "x" in lowered:
        left, _, right = lowered.partition("x")
        if left.isdigit() and right.isdigit():
            width = int(left)
            height = int(right)
            if width > 0 and height > 0:
                divisor = math.gcd(width, height)
                return f"{width // divisor}:{height // divisor}"
    return text


class EvolinkImageGenerator:
    """Generate images through the Evolink async image task API."""

    supports_concurrency = True

    MODEL_ALIASES = {
        "flash": "gemini-3.1-flash-image-preview",
        "nanobanana-2": "gemini-3.1-flash-image-preview",
        "nano-banana-2": "gemini-3.1-flash-image-preview",
    }

    def __init__(
        self,
        *,
        output_dir: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        _load_env_fallbacks()

        self.base_url = str(base_url or os.getenv("EVOLINK_BASE_URL") or "https://api.evolink.ai").strip().rstrip("/")
        self.api_key = str(api_key or os.getenv("EVOLINK_API_KEY") or "").strip()
        requested_model = str(model or os.getenv("EVOLINK_IMAGE_MODEL") or "gemini-3.1-flash-image-preview").strip()
        self.model_candidates = self._build_model_candidates(requested_model)
        self.model_name = requested_model
        self.model_id = self.model_candidates[0]
        self.size = _normalize_size(os.getenv("EVOLINK_IMAGE_SIZE"))
        self.quality = _normalize_quality(os.getenv("EVOLINK_IMAGE_QUALITY"))
        self.timeout_sec = max(5, int(os.getenv("EVOLINK_TIMEOUT_SEC", "45")))
        self.poll_interval_sec = max(1, int(os.getenv("EVOLINK_POLL_INTERVAL_SEC", "3")))
        self.poll_timeout_sec = max(self.poll_interval_sec, int(os.getenv("EVOLINK_POLL_TIMEOUT_SEC", "300")))
        self.verify_ssl = _as_bool(os.getenv("EVOLINK_VERIFY_SSL"), default=True)
        self.network_retry = max(1, int(os.getenv("EVOLINK_NETWORK_RETRY", "3")))
        self.output_dir = Path(output_dir or Path(__file__).resolve().parent.parent / "generated_images")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session: Optional[requests.Session] = None
        self.last_error: Optional[str] = None
        self.last_error_code: Optional[str] = None
        self.last_task_id: Optional[str] = None
        self.provider_name = "evolink"

    @classmethod
    def _build_model_candidates(cls, requested_model: str) -> list[str]:
        candidates: list[str] = []
        raw = str(requested_model or "").strip()
        if raw:
            candidates.append(raw)
        mapped = cls.MODEL_ALIASES.get(raw.lower()) if raw else None
        if mapped and mapped not in candidates:
            candidates.append(mapped)
        if raw == "nano-banana-2-beta" and "gemini-3.1-flash-image-preview" not in candidates:
            candidates.append("gemini-3.1-flash-image-preview")
        if not candidates:
            candidates.append("gemini-3.1-flash-image-preview")
        return candidates

    def _set_error(self, code: str, message: str) -> None:
        self.last_error_code = str(code or "").strip() or "unknown_error"
        self.last_error = str(message or "").strip() or self.last_error_code

    async def start(self) -> None:
        if not self.api_key:
            raise ValueError("Missing EVOLINK_API_KEY")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        print(f"Initializing Evolink image client (model: {self.model_id})...")
        print(f"  Base URL: {self.base_url}")
        print("  [OK] Evolink image client initialized")

    def _request(self, method: str, path: str, *, json_body: dict | None = None, timeout: int | None = None) -> requests.Response:
        if not self.session:
            raise RuntimeError("Client not initialized")
        last_exc: Exception | None = None
        url = path if str(path).startswith(("http://", "https://")) else f"{self.base_url}{path}"
        for attempt in range(1, self.network_retry + 1):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    json=json_body,
                    timeout=timeout or self.timeout_sec,
                    verify=self.verify_ssl,
                )
            except requests.RequestException as exc:
                last_exc = exc
                if attempt >= self.network_retry:
                    raise
                time.sleep(min(attempt, 3))
                continue
            if response.status_code in {429, 500, 502, 503} and attempt < self.network_retry:
                time.sleep(min(attempt, 3))
                continue
            return response
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"Request failed: {method} {url}")

    def _parse_error_response(self, response: requests.Response) -> tuple[str, str]:
        try:
            payload = response.json()
        except Exception:
            text = response.text.strip()
            return f"http_{response.status_code}", text or f"HTTP {response.status_code}"
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            code = str(error.get("code") or f"http_{response.status_code}").strip()
            message = str(error.get("message") or code).strip()
            return code, message
        return f"http_{response.status_code}", response.text.strip() or f"HTTP {response.status_code}"

    def _submit_generation(self, prompt: str, model_id: str, *, size: str | None = None) -> str | None:
        payload = {
            "model": model_id,
            "prompt": prompt,
            "size": _normalize_size(size) if size else self.size,
            "quality": self.quality,
        }
        response = self._request("POST", "/v1/images/generations", json_body=payload, timeout=self.timeout_sec)
        if response.status_code >= 400:
            code, message = self._parse_error_response(response)
            self._set_error(code, message)
            return None
        data = response.json()
        task_id = str(data.get("id") or "").strip()
        if not task_id:
            self._set_error("missing_task_id", f"Unexpected response: {data!r}")
            return None
        self.last_task_id = task_id
        self.model_id = str(data.get("model") or model_id).strip() or model_id
        return task_id

    def _poll_task_results(self, task_id: str) -> list[str] | None:
        deadline = time.time() + self.poll_timeout_sec
        while time.time() < deadline:
            response = self._request("GET", f"/v1/tasks/{task_id}", timeout=self.timeout_sec)
            if response.status_code >= 400:
                code, message = self._parse_error_response(response)
                self._set_error(code, message)
                return None
            data = response.json()
            status = str(data.get("status") or "").strip().lower()
            if status == "completed":
                results = data.get("results")
                if isinstance(results, list) and results:
                    return [str(item).strip() for item in results if str(item or "").strip()]
                output = data.get("output")
                if isinstance(output, dict):
                    image_urls = output.get("image_urls")
                    if isinstance(image_urls, list) and image_urls:
                        return [str(item).strip() for item in image_urls if str(item or "").strip()]
                self._set_error("missing_results", f"Task completed without results: {data!r}")
                return None
            if status == "failed":
                error = data.get("error") if isinstance(data, dict) else None
                if isinstance(error, dict):
                    self._set_error(str(error.get("code") or "task_failed"), str(error.get("message") or "task_failed"))
                else:
                    self._set_error("task_failed", f"Task failed: {data!r}")
                return None
            if status not in {"pending", "processing"}:
                self._set_error("unexpected_status", f"Unexpected task status: {status or data!r}")
                return None
            time.sleep(self.poll_interval_sec)
        self._set_error("poll_timeout", f"Timed out waiting for task {task_id}")
        return None

    def _download_image(self, image_url: str, account: str, page_type: str) -> str | None:
        response = self._request("GET", image_url, timeout=self.timeout_sec)
        if response.status_code >= 400:
            code, message = self._parse_error_response(response)
            self._set_error(code, message)
            return None
        suffix = Path(urlparse(image_url).path).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if "jpeg" in content_type or "jpg" in content_type:
                suffix = ".jpg"
            elif "webp" in content_type:
                suffix = ".webp"
            else:
                suffix = ".png"
        filename = f"{account}-{page_type}-{int(time.time())}{suffix}"
        output_path = self.output_dir / filename
        output_path.write_bytes(response.content)
        return str(output_path)

    def _generate_image_sync(self, prompt: str, account: str, page_type: str, size: str | None = None) -> str | None:
        self.last_error = None
        self.last_error_code = None
        self.last_task_id = None

        for model_id in self.model_candidates:
            try:
                task_id = self._submit_generation(prompt, model_id, size=size)
            except requests.RequestException as exc:
                self._set_error("network_request_failed", str(exc))
                task_id = None
            if task_id:
                try:
                    results = self._poll_task_results(task_id)
                except requests.RequestException as exc:
                    self._set_error("network_request_failed", str(exc))
                    return None
                if not results:
                    return None
                image_url = results[0]
                try:
                    downloaded = self._download_image(image_url, account, page_type)
                except requests.RequestException as exc:
                    self._set_error("network_request_failed", str(exc))
                    return None
                if downloaded:
                    return downloaded
                return None
            if self.last_error_code not in {"model_access_denied", "invalid_request"}:
                break
        return None

    async def generate_image(
        self,
        prompt: str,
        account: str = "A",
        page_type: str = "cover",
        size: str | None = None,
    ) -> str | None:
        return await asyncio.to_thread(self._generate_image_sync, prompt, account, page_type, size)

    async def close(self) -> None:
        if self.session is not None:
            self.session.close()
            self.session = None
