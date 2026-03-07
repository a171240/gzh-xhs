#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Publish WeChat Official Account content with Playwright."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen


SKILL_NAME = "wechat-publish-playwright"
SCRIPT_DIR = Path(__file__).resolve().parent

DEFAULT_SELECTORS: dict[str, Any] = {
    "entry_url": "https://mp.weixin.qq.com/",
    "compose_url": "https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit_v2&action=edit&isNew=1&type=77&createType=0",
    "logged_in_hint_selector": "body",
    "new_article_button": "a:has-text('新建图文'), a:has-text('新建图文消息')",
    "content_menu_button": "a:has-text('内容管理'), a:has-text('内容与互动')",
    "article_manage_button": "a:has-text('图文消息'), a:has-text('内容库')",
    "draft_box_button": "a:has-text('草稿箱')",
    "quick_article_button": "a:has-text('文章'), div:has-text('文章')",
    "title_input": "textarea#title, textarea[name='title'], textarea[placeholder*='标题']",
    "author_input": "input#author, input[name='author'], input[placeholder*='作者']",
    "content_editor": "div.ProseMirror[contenteditable='true']",
    "content_textarea": "textarea",
    "cover_file_input": "input[type='file']",
    "save_draft_button": "button:has-text('保存为草稿'), span:has-text('保存为草稿')",
    "publish_button": "button:has-text('发表')",
    "publish_confirm_button": "button:has-text('确定')",
    "schedule_button": "button:has-text('定时发表')",
    "schedule_time_input": "input[placeholder*='时间']",
    "schedule_confirm_button": "button:has-text('确定')",
    "inline_image_button": "button:has-text('图片'), span:has-text('图片'), div[role='button']:has-text('图片')",
    "inline_image_file_input": "input[type='file'][accept*='image'], input[type='file'][accept*='png'], input[type='file'][accept*='jpg'], input[type='file'][accept*='jpeg']",
    "inline_image_upload_wait_ms": 12000,
    "manual_publish_hint_selector": "text=管理员确认, text=群发申请",
}


class PublishError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8-sig")


def load_selectors(input_payload: dict[str, Any]) -> dict[str, Any]:
    selectors = dict(DEFAULT_SELECTORS)
    selectors_path = str(input_payload.get("selectors_path") or "").strip()
    if selectors_path:
        selectors.update(load_json(Path(selectors_path)))
        return selectors

    fallback = SCRIPT_DIR / "config" / "selectors.wechat.json"
    if fallback.exists():
        selectors.update(load_json(fallback))
    return selectors


def extract_id_from_url(url: str, key: str) -> str:
    query = parse_qs(urlparse(url).query)
    values = query.get(key)
    return values[0] if values else ""


def sanitize_error(message: str) -> str:
    return re.sub(r"\s+", " ", message).strip()[:600]


def capture(page: Any, screenshots_dir: Path, run_log: dict[str, Any], name: str) -> None:
    path = screenshots_dir / f"{len(run_log['screenshot_paths']) + 1:02d}-{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True, timeout=10000)
        run_log["screenshot_paths"].append(str(path))
        return
    except Exception:
        pass
    try:
        page.screenshot(path=str(path), full_page=False, timeout=10000)
        run_log["screenshot_paths"].append(str(path))
    except Exception:
        return


def require_text_field(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        raise PublishError("INVALID_INPUT", f"Missing required field: {key}")
    return value


def get_mode(payload: dict[str, Any]) -> str:
    mode = str(payload.get("mode") or "").strip().lower()
    if not mode:
        mode = "schedule" if payload.get("publish_time") else "draft"
    if mode not in {"draft", "publish", "schedule"}:
        raise PublishError("INVALID_INPUT", "mode must be one of draft/publish/schedule")
    return mode


def _entry_host(url: str) -> str:
    try:
        return str(urlparse(url).netloc or "").lower().strip()
    except Exception:
        return ""


def _pick_bitbrowser_page(context: Any, entry_url: str) -> Any:
    pages = list(context.pages or [])
    if not pages:
        return context.new_page()
    host = _entry_host(entry_url)
    if host:
        editor_url_hints = ("appmsg_edit", "action=edit", "type=77", "/cgi-bin/appmsg")
        for page in pages:
            page_url = str(getattr(page, "url", "") or "").lower()
            if host in page_url and any(hint in page_url for hint in editor_url_hints):
                return page
        for page in pages:
            page_url = str(getattr(page, "url", "") or "").lower()
            if host in page_url and ("token=" in page_url or "/cgi-bin/" in page_url):
                return page
        for page in pages:
            page_url = str(getattr(page, "url", "") or "").lower()
            if host in page_url:
                return page
    return pages[0]


def _compose_url_with_session(entry_url: str, compose_url: str) -> str:
    compose = str(compose_url or "").strip()
    if not compose:
        return ""
    try:
        compose_parsed = urlparse(compose)
        compose_q = parse_qs(compose_parsed.query)
        entry_q = parse_qs(urlparse(str(entry_url or "")).query)
        for key in ("token", "lang"):
            if key not in compose_q and entry_q.get(key):
                compose_q[key] = [entry_q[key][0]]
        merged_query = urlencode({k: (v[0] if isinstance(v, list) and v else v) for k, v in compose_q.items()})
        return urlunparse(
            (
                compose_parsed.scheme,
                compose_parsed.netloc,
                compose_parsed.path,
                compose_parsed.params,
                merged_query,
                compose_parsed.fragment,
            )
        )
    except Exception:
        return compose


def _iter_locator(locator: Any, *, reverse: bool = False) -> list[Any]:
    count = locator.count()
    indexes = range(count - 1, -1, -1) if reverse else range(count)
    return [locator.nth(idx) for idx in indexes]


def _fill_first_visible(page: Any, selector: str, value: str, *, timeout_ms: int = 15000) -> None:
    locator = page.locator(selector)
    for item in _iter_locator(locator):
        try:
            if item.is_visible(timeout=1000):
                item.fill(value, timeout=timeout_ms)
                return
        except Exception:
            continue
    raise PublishError("SELECTOR_NOT_VISIBLE", f"No visible element for selector: {selector}")


def _click_first_visible(page: Any, selector: str, *, timeout_ms: int = 12000) -> bool:
    locator = page.locator(selector)
    for item in _iter_locator(locator):
        try:
            if item.is_visible(timeout=1000):
                item.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


def _has_visible(page: Any, selector: str) -> bool:
    locator = page.locator(selector)
    for item in _iter_locator(locator):
        try:
            if item.is_visible(timeout=500):
                return True
        except Exception:
            continue
    return False


def _click_by_visible_text(page: Any, text: str) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                (targetText) => {
                  const nodes = Array.from(document.querySelectorAll('a,button,div,span'));
                  for (const node of nodes) {
                    const txt = String(node.innerText || '').trim();
                    if (txt !== targetText) continue;
                    const rect = node.getBoundingClientRect();
                    const style = window.getComputedStyle(node);
                    if (rect.width <= 0 || rect.height <= 0) continue;
                    if (style.visibility === 'hidden' || style.display === 'none') continue;
                    node.click();
                    return true;
                  }
                  return false;
                }
                """,
                str(text or "").strip(),
            )
        )
    except Exception:
        return False


def _try_open_wechat_editor(page: Any, selectors: dict[str, Any]) -> bool:
    title_selector = str(selectors["title_input"])
    if _has_visible(page, title_selector):
        return True

    quick_article = str(selectors.get("quick_article_button") or "").strip()
    new_article_candidates = [
        str(selectors.get("new_article_button") or "").strip(),
        quick_article,
        "a:has-text('新建图文消息')",
        "button:has-text('新建图文')",
        "text=文章",
    ]
    for candidate in new_article_candidates:
        if candidate and _click_first_visible(page, candidate, timeout_ms=5000):
            page.wait_for_timeout(1000)
            if _has_visible(page, title_selector):
                return True

    for text in ("文章", "内容管理", "内容库", "图文消息", "新建图文", "新建图文消息"):
        if _click_by_visible_text(page, text):
            page.wait_for_timeout(1000)
            if _has_visible(page, title_selector):
                return True

    content_menu = str(selectors.get("content_menu_button") or "").strip()
    if content_menu and _click_first_visible(page, content_menu, timeout_ms=6000):
        page.wait_for_timeout(800)
    article_manage = str(selectors.get("article_manage_button") or "").strip()
    if article_manage and _click_first_visible(page, article_manage, timeout_ms=6000):
        page.wait_for_timeout(800)
    draft_box = str(selectors.get("draft_box_button") or "").strip()
    if draft_box and _click_first_visible(page, draft_box, timeout_ms=5000):
        page.wait_for_timeout(800)

    for candidate in new_article_candidates:
        if candidate and _click_first_visible(page, candidate, timeout_ms=5000):
            page.wait_for_timeout(1000)
            if _has_visible(page, title_selector):
                return True
    return _has_visible(page, title_selector)


def _session_expired(page: Any) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                () => {
                  const text = String(document.body ? document.body.innerText || '' : '').trim();
                  return text.includes('登录超时') || text.includes('请重新登录') || text.includes('重新登录');
                }
                """
            )
        )
    except Exception:
        return False


def _is_not_found_page(page: Any) -> bool:
    try:
        return bool(
            page.evaluate(
                """
                () => {
                  const text = String(document.body ? document.body.innerText || '' : '').trim();
                  return text.includes('404') || text.includes('页面不存在') || text.includes('返回首页');
                }
                """
            )
        )
    except Exception:
        return False


def _bitbrowser_headers(cfg: dict[str, Any]) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Language": str(cfg.get("language") or os.getenv("BITBROWSER_LANGUAGE") or "zh"),
    }
    api_key = str(cfg.get("api_key") or os.getenv("BITBROWSER_API_KEY") or os.getenv("BITBROWSER_LOCAL_API_TOKEN") or "").strip()
    if api_key:
        headers["X-API-KEY"] = api_key
    return headers


def _bitbrowser_post(cfg: dict[str, Any], path: str, payload: dict[str, Any]) -> dict[str, Any]:
    base = str(cfg.get("api_base") or os.getenv("BITBROWSER_API_BASE") or os.getenv("BITBROWSER_LOCAL_API_BASE") or "http://127.0.0.1:54345").rstrip("/")
    timeout_sec = int(cfg.get("timeout_sec") or os.getenv("BITBROWSER_TIMEOUT_SEC") or 20)
    req = Request(
        url=f"{base}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=_bitbrowser_headers(cfg),
        method="POST",
    )
    try:
        with urlopen(req, timeout=max(3, timeout_sec)) as resp:
            raw = resp.read()
    except HTTPError as exc:
        body = ""
        try:
            body = (exc.read() or b"").decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise PublishError("BITBROWSER_HTTP_ERROR", f"BitBrowser API HTTP {exc.code}: {body or exc.reason}") from exc
    except URLError as exc:
        raise PublishError("BITBROWSER_CONNECT_ERROR", f"BitBrowser API request failed: {exc}") from exc

    try:
        parsed = json.loads((raw or b"{}").decode("utf-8", errors="replace"))
    except Exception as exc:
        raise PublishError("BITBROWSER_INVALID_JSON", "BitBrowser API returned non-JSON response") from exc
    if not isinstance(parsed, dict):
        raise PublishError("BITBROWSER_INVALID_RESPONSE", "BitBrowser API returned invalid response")
    if not parsed.get("success", False):
        raise PublishError("BITBROWSER_CALL_FAILED", str(parsed.get("msg") or f"BitBrowser API call failed: {path}"))
    data = parsed.get("data")
    return data if isinstance(data, dict) else {}


def _bitbrowser_open_profile(cfg: dict[str, Any], profile_id: str) -> dict[str, Any]:
    data = _bitbrowser_post(cfg, "/browser/open", {"id": profile_id})
    ws_url = str(data.get("ws") or data.get("wsUrl") or "").strip()
    if not ws_url:
        raise PublishError("BITBROWSER_WS_MISSING", "BitBrowser open succeeded but did not return ws endpoint")
    return data


def _bitbrowser_close_profile(cfg: dict[str, Any], profile_id: str) -> None:
    try:
        _bitbrowser_post(cfg, "/browser/close", {"id": profile_id})
    except Exception:
        return


def _validate_blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = payload.get("content_blocks") or []
    if blocks and not isinstance(blocks, list):
        raise PublishError("INVALID_INPUT", "content_blocks must be a list")
    normalized: list[dict[str, Any]] = []
    for idx, raw in enumerate(blocks, 1):
        if not isinstance(raw, dict):
            raise PublishError("INVALID_INPUT", f"content_blocks[{idx}] must be an object")
        block_type = str(raw.get("type") or "").strip().lower()
        if block_type == "html":
            html_value = str(raw.get("html") or "").strip()
            if not html_value:
                raise PublishError("INVALID_INPUT", f"content_blocks[{idx}] html block is empty")
            normalized.append({"type": "html", "role": str(raw.get("role") or "body"), "html": html_value})
            continue
        if block_type == "image":
            image_path = Path(str(raw.get("path") or "")).expanduser()
            if not image_path.exists():
                raise PublishError("INVALID_INPUT", f"content_blocks[{idx}] image not found: {image_path}")
            normalized.append(
                {
                    "type": "image",
                    "role": str(raw.get("role") or "body_image"),
                    "path": str(image_path.resolve()),
                    "alt": str(raw.get("alt") or "正文配图"),
                }
            )
            continue
        raise PublishError("INVALID_INPUT", f"content_blocks[{idx}] type must be html/image")
    return normalized


def _editor_image_count(page: Any, selector: str) -> int:
    try:
        return int(
            page.evaluate(
                """
                (editorSelector) => {
                  const node = document.querySelector(editorSelector);
                  if (!node) return 0;
                  return node.querySelectorAll('img').length;
                }
                """,
                selector,
            )
        )
    except Exception:
        return 0


def _clear_editor(page: Any, selector: str) -> None:
    page.evaluate(
        """
        (editorSelector) => {
          const node = document.querySelector(editorSelector);
          if (!node) throw new Error(`Missing selector: ${editorSelector}`);
          const doc = node.ownerDocument || document;
          const createAnchor = () => {
            const p = doc.createElement('p');
            p.setAttribute('data-codex-anchor', '1');
            const br = doc.createElement('br');
            p.appendChild(br);
            return p;
          };
          const placeCaret = (target) => {
            const selection = doc.getSelection();
            const range = doc.createRange();
            range.selectNodeContents(target);
            range.collapse(false);
            if (selection) {
              selection.removeAllRanges();
              selection.addRange(range);
            }
          };
          node.focus();
          node.innerHTML = '';
          const anchor = createAnchor();
          node.appendChild(anchor);
          placeCaret(anchor);
          node.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, inputType: 'deleteContentBackward', data: null }));
          node.dispatchEvent(new Event('change', { bubbles: true }));
        }
        """,
        selector,
    )


def _ensure_editor_anchor(page: Any, selector: str) -> None:
    page.evaluate(
        """
        (editorSelector) => {
          const node = document.querySelector(editorSelector);
          if (!node) throw new Error(`Missing selector: ${editorSelector}`);
          const doc = node.ownerDocument || document;
          const isAnchor = (target) => {
            return !!target
              && target.nodeType === 1
              && target.tagName === 'P'
              && target.getAttribute('data-codex-anchor') === '1';
          };
          const createAnchor = () => {
            const p = doc.createElement('p');
            p.setAttribute('data-codex-anchor', '1');
            const br = doc.createElement('br');
            p.appendChild(br);
            return p;
          };
          const placeCaret = (target) => {
            const selection = doc.getSelection();
            const range = doc.createRange();
            range.selectNodeContents(target);
            range.collapse(false);
            if (selection) {
              selection.removeAllRanges();
              selection.addRange(range);
            }
          };

          node.focus();
          let anchor = node.lastElementChild;
          if (!isAnchor(anchor)) {
            anchor = createAnchor();
            node.appendChild(anchor);
          }
          placeCaret(anchor);
          node.dispatchEvent(new Event('change', { bubbles: true }));
        }
        """,
        selector,
    )


def _cleanup_editor_anchors(page: Any, selector: str) -> None:
    page.evaluate(
        """
        (editorSelector) => {
          const node = document.querySelector(editorSelector);
          if (!node) throw new Error(`Missing selector: ${editorSelector}`);
          const anchors = Array.from(node.querySelectorAll('p[data-codex-anchor="1"]'));
          for (const anchor of anchors) {
            const hasMeaningfulChild = Array.from(anchor.childNodes).some((child) => {
              if (child.nodeType === 3) {
                return String(child.textContent || '').trim().length > 0;
              }
              if (child.nodeType !== 1) {
                return false;
              }
              const tag = String(child.tagName || '').toUpperCase();
              return tag !== 'BR';
            });
            if (hasMeaningfulChild) {
              anchor.removeAttribute('data-codex-anchor');
              continue;
            }
            anchor.remove();
          }
          node.dispatchEvent(new Event('change', { bubbles: true }));
        }
        """,
        selector,
    )


def _append_editor_html(page: Any, selector: str, html_fragment: str) -> None:
    page.evaluate(
        """
        ({selector, html}) => {
          const node = document.querySelector(selector);
          if (!node) throw new Error(`Missing selector: ${selector}`);
          const doc = node.ownerDocument || document;
          const isAnchor = (target) => {
            return !!target
              && target.nodeType === 1
              && target.tagName === 'P'
              && target.getAttribute('data-codex-anchor') === '1';
          };
          const createAnchor = () => {
            const p = doc.createElement('p');
            p.setAttribute('data-codex-anchor', '1');
            const br = doc.createElement('br');
            p.appendChild(br);
            return p;
          };
          const moveCursorToEnd = () => {
            const selection = doc.getSelection();
            const range = doc.createRange();
            range.selectNodeContents(node);
            range.collapse(false);
            if (selection) {
              selection.removeAllRanges();
              selection.addRange(range);
            }
          };
          const appendFragment = () => {
            const template = doc.createElement('template');
            template.innerHTML = String(html || '');
            const fragment = template.content.cloneNode(true);
            const children = Array.from(fragment.childNodes).filter((child) => {
              return !(child.nodeType === 3 && !String(child.textContent || '').trim());
            });
            let anchor = node.lastElementChild;
            if (!isAnchor(anchor)) {
              anchor = createAnchor();
              node.appendChild(anchor);
            }
            children.forEach((child) => {
              node.insertBefore(child, anchor);
            });
            return anchor;
          };
          node.focus();
          let anchor = null;
          try {
            anchor = appendFragment();
          } catch (_err) {
            node.insertAdjacentHTML('beforeend', html);
            anchor = node.lastElementChild;
          }
          if (anchor) {
            const selection = doc.getSelection();
            const range = doc.createRange();
            range.selectNodeContents(anchor);
            range.collapse(false);
            if (selection) {
              selection.removeAllRanges();
              selection.addRange(range);
            }
          } else {
            moveCursorToEnd();
          }
          node.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, inputType: 'insertFromPaste', data: null }));
          node.dispatchEvent(new Event('change', { bubbles: true }));
        }
        """,
        {"selector": selector, "html": html_fragment},
    )


def _set_input_files(locator: Any, file_path: Path, *, reverse: bool = False) -> None:
    errors: list[str] = []
    for item in _iter_locator(locator, reverse=reverse):
        try:
            item.set_input_files(str(file_path), timeout=15000)
            return
        except Exception as exc:
            errors.append(str(exc))
    raise PublishError("SELECTOR_NOT_VISIBLE", f"Cannot set file input for {file_path.name}: {' | '.join(errors[:2])}")


def _upload_inline_image(page: Any, selectors: dict[str, Any], image_path: Path, *, index: int) -> None:
    button_selector = str(selectors.get("inline_image_button") or "").strip()
    file_selector = str(selectors.get("inline_image_file_input") or "").strip()
    if not file_selector:
        raise PublishError("INLINE_IMAGE_SELECTOR_MISSING", "inline_image_file_input selector is required")

    editor_selector = str(selectors["content_editor"])
    _ensure_editor_anchor(page, editor_selector)
    before_count = _editor_image_count(page, editor_selector)
    if button_selector and not _click_first_visible(page, button_selector, timeout_ms=5000):
        raise PublishError("INLINE_IMAGE_SELECTOR_MISSING", f"Cannot find inline image button: {button_selector}")
    page.wait_for_timeout(600)
    _set_input_files(page.locator(file_selector), image_path, reverse=True)

    wait_ms = int(selectors.get("inline_image_upload_wait_ms") or 12000)
    deadline = time.monotonic() + max(2.0, wait_ms / 1000.0)
    while time.monotonic() < deadline:
        page.wait_for_timeout(500)
        after_count = _editor_image_count(page, editor_selector)
        if after_count > before_count:
            _ensure_editor_anchor(page, editor_selector)
            return
    raise PublishError("INLINE_IMAGE_UPLOAD_FAILED", f"Inline image upload did not appear in editor: {image_path.name} (block {index})")


def _insert_content(page: Any, selectors: dict[str, Any], payload: dict[str, Any]) -> None:
    blocks = _validate_blocks(payload)
    editor_selector = str(selectors["content_editor"])

    if blocks:
        _clear_editor(page, editor_selector)
        for index, block in enumerate(blocks, 1):
            if block["type"] == "html":
                _append_editor_html(page, editor_selector, str(block["html"]))
                page.wait_for_timeout(150)
                continue
            _upload_inline_image(page, selectors, Path(str(block["path"])), index=index)
        _cleanup_editor_anchors(page, editor_selector)
        return

    content_html = str(payload.get("content_html") or "").strip()
    content_md = str(payload.get("content_md") or "").strip()
    if not content_md and not content_html:
        raise PublishError("INVALID_INPUT", "Either content_blocks, content_html or content_md is required")

    if content_html:
        _clear_editor(page, editor_selector)
        _append_editor_html(page, editor_selector, content_html)
        _cleanup_editor_anchors(page, editor_selector)
        return
    if selectors.get("content_textarea"):
        _fill_first_visible(page, str(selectors["content_textarea"]), content_md, timeout_ms=30000)
        return
    page.evaluate(
        """
        ({selector, text}) => {
          const node = document.querySelector(selector);
          if (!node) throw new Error(`Missing selector: ${selector}`);
          node.focus();
          node.textContent = text;
        }
        """,
        {"selector": editor_selector, "text": content_md},
    )


def run_publish(payload: dict[str, Any], selectors: dict[str, Any], workspace: Path, dry_run: bool) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    date_folder = local_date()
    reports_dir = workspace / "reports" / date_folder
    screenshots_dir = reports_dir / "screenshots" / run_id
    run_log_path = reports_dir / f"{SKILL_NAME}-{run_id}.json"

    run_log: dict[str, Any] = {
        "run_id": run_id,
        "skill_name": SKILL_NAME,
        "start_time": now_iso(),
        "end_time": "",
        "status": "running",
        "error_code": "",
        "error_message": "",
        "account_name": str(payload.get("account_name") or "").strip(),
        "layout_profile": str(payload.get("layout_profile") or ""),
        "screenshot_paths": [],
    }

    result: dict[str, Any] = {
        "status": "failed",
        "draft_id": "",
        "post_id": "",
        "publish_url": "",
        "account_name": str(payload.get("account_name") or "").strip(),
        "screenshot_paths": [],
        "run_log": str(run_log_path),
    }

    page = None
    context = None
    browser = None
    opened_bit_profile = ""
    close_bit_profile = False

    try:
        title = require_text_field(payload, "title")
        mode = get_mode(payload)
        _validate_blocks(payload)
        bitbrowser_cfg = dict(payload.get("bitbrowser") or {})
        bit_profile_id = str(bitbrowser_cfg.get("profile_id") or "").strip()
        use_bitbrowser = bool(bit_profile_id)
        run_log["channel"] = "bitbrowser" if use_bitbrowser else "playwright_persistent_profile"
        if use_bitbrowser:
            run_log["bitbrowser_profile_id"] = bit_profile_id
            run_log["bitbrowser_profile_name"] = str(bitbrowser_cfg.get("profile_name") or payload.get("account_name") or "").strip()
            result["channel"] = run_log["channel"]
            result["bitbrowser_profile_id"] = bit_profile_id
            result["bitbrowser_profile_name"] = str(bitbrowser_cfg.get("profile_name") or payload.get("account_name") or "").strip()
        else:
            result["channel"] = run_log["channel"]
        if mode == "schedule" and not str(payload.get("publish_time", "")).strip():
            raise PublishError("INVALID_INPUT", "publish_time is required when mode=schedule")

        cover_path = str(payload.get("cover_path") or "").strip()
        if cover_path and not Path(cover_path).exists():
            raise PublishError("INVALID_INPUT", f"cover_path not found: {cover_path}")

        if dry_run:
            run_log["status"] = "dry_run"
            run_log["end_time"] = now_iso()
            write_json(run_log_path, run_log)
            result["status"] = "dry_run"
            return result

        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover
            raise PublishError(
                "IMPORT_ERROR",
                "Playwright is not available. Install with: pip install playwright && playwright install chromium",
            ) from exc

        profile = dict(payload.get("account_profile") or {})
        user_data_dir = Path(profile.get("user_data_dir") or (workspace / "profiles" / "wechat-default"))
        headless = bool(profile.get("headless", False))
        slow_mo = int(profile.get("slow_mo", 0))
        login_timeout_sec = int(profile.get("login_timeout_sec", 120))
        cdp_timeout_ms = int(bitbrowser_cfg.get("cdp_timeout_ms") or os.getenv("BITBROWSER_CDP_TIMEOUT_MS") or 30000)

        with sync_playwright() as p:
            if use_bitbrowser:
                open_info = _bitbrowser_open_profile(bitbrowser_cfg, bit_profile_id)
                opened_bit_profile = bit_profile_id
                ws_url = str(open_info.get("ws") or open_info.get("wsUrl") or "").strip()
                close_bit_profile = bool(bitbrowser_cfg.get("close_after_publish", False))
                browser = p.chromium.connect_over_cdp(ws_url, timeout=max(1000, cdp_timeout_ms))
                if not browser.contexts:
                    raise PublishError("BITBROWSER_CONTEXT_MISSING", "BitBrowser returned ws endpoint but no browser context is available")
                context = browser.contexts[0]
                page = _pick_bitbrowser_page(context, str(selectors["entry_url"]))
            else:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(user_data_dir),
                    headless=headless,
                    slow_mo=slow_mo,
                    viewport={"width": 1440, "height": 900},
                )
                page = context.pages[0] if context.pages else context.new_page()

            page_url = str(getattr(page, "url", "") or "").strip().lower()
            target_host = _entry_host(str(selectors["entry_url"]))
            if not (use_bitbrowser and page_url and target_host and target_host in page_url):
                page.goto(str(selectors["entry_url"]), wait_until="domcontentloaded")
            capture(page, screenshots_dir, run_log, "entry")

            try:
                page.wait_for_selector(str(selectors["logged_in_hint_selector"]), timeout=login_timeout_sec * 1000)
            except PlaywrightTimeoutError as exc:
                raise PublishError("LOGIN_TIMEOUT", "Login state not detected in allowed time") from exc

            compose_url = str(selectors.get("compose_url") or "").strip()
            editor_already_open = _has_visible(page, str(selectors["title_input"])) and _has_visible(page, str(selectors["content_editor"]))
            if compose_url and not editor_already_open:
                compose_target = _compose_url_with_session(page.url, compose_url)
                page.goto(compose_target, wait_until="domcontentloaded")
                capture(page, screenshots_dir, run_log, "compose")
                if _session_expired(page):
                    raise PublishError("LOGIN_EXPIRED", "WeChat session expired in persistent profile, please re-login")
                if _is_not_found_page(page):
                    run_log["compose_fallback"] = True
                    page.goto(str(selectors["entry_url"]), wait_until="domcontentloaded")
                    capture(page, screenshots_dir, run_log, "compose-fallback")

            if not _try_open_wechat_editor(page, selectors):
                if _session_expired(page):
                    raise PublishError("LOGIN_EXPIRED", "WeChat session expired in persistent profile, please re-login")
                raise PublishError("EDITOR_NOT_READY", "Cannot open WeChat editor from current page/session")

            _click_first_visible(page, "button:has-text('记住了'), button:has-text('我知道了')", timeout_ms=3000)
            _fill_first_visible(page, str(selectors["title_input"]), title, timeout_ms=30000)
            if payload.get("author"):
                try:
                    _fill_first_visible(page, str(selectors["author_input"]), str(payload["author"]), timeout_ms=15000)
                except PublishError:
                    run_log["author_input_skipped"] = True

            _insert_content(page, selectors, payload)

            if cover_path:
                _set_input_files(page.locator(str(selectors["cover_file_input"])), Path(cover_path))

            capture(page, screenshots_dir, run_log, "filled")

            if not _click_first_visible(page, str(selectors["save_draft_button"]), timeout_ms=12000):
                raise PublishError("SELECTOR_NOT_VISIBLE", f"Cannot find visible save draft button: {selectors['save_draft_button']}")
            page.wait_for_timeout(1600)
            capture(page, screenshots_dir, run_log, "saved-draft")

            if mode in {"publish", "schedule"}:
                if not _click_first_visible(page, str(selectors["publish_button"]), timeout_ms=12000):
                    raise PublishError("SELECTOR_NOT_VISIBLE", f"Cannot find visible publish button: {selectors['publish_button']}")
                page.wait_for_timeout(800)

                if mode == "schedule":
                    if not _click_first_visible(page, str(selectors["schedule_button"]), timeout_ms=10000):
                        raise PublishError("SELECTOR_NOT_VISIBLE", f"Cannot find visible schedule button: {selectors['schedule_button']}")
                    _fill_first_visible(page, str(selectors["schedule_time_input"]), str(payload["publish_time"]), timeout_ms=10000)
                    if not _click_first_visible(page, str(selectors["schedule_confirm_button"]), timeout_ms=10000):
                        raise PublishError("SELECTOR_NOT_VISIBLE", f"Cannot find visible schedule confirm button: {selectors['schedule_confirm_button']}")
                else:
                    confirm_selector = str(selectors.get("publish_confirm_button") or "").strip()
                    if confirm_selector:
                        _click_first_visible(page, confirm_selector, timeout_ms=5000)

                page.wait_for_timeout(1200)
                capture(page, screenshots_dir, run_log, f"{mode}-submitted")

            current_url = page.url
            result["publish_url"] = current_url
            result["draft_id"] = extract_id_from_url(current_url, "draft_id") or extract_id_from_url(current_url, "appmsgid")
            result["post_id"] = extract_id_from_url(current_url, "post_id") or result["draft_id"]
            manual_hint_selector = str(selectors.get("manual_publish_hint_selector") or "").strip()
            if manual_hint_selector and _has_visible(page, manual_hint_selector):
                result["status"] = "waiting_manual_publish"
            else:
                result["status"] = "success"
            run_log["status"] = "success"
            result["channel"] = run_log["channel"]
    except PublishError as exc:
        run_log["status"] = "failed"
        run_log["error_code"] = exc.code
        run_log["error_message"] = sanitize_error(str(exc))
    except Exception as exc:  # pragma: no cover
        run_log["status"] = "failed"
        run_log["error_code"] = "RUNTIME_ERROR"
        run_log["error_message"] = sanitize_error(f"{exc}\n{traceback.format_exc()}")
    finally:
        if page is not None and run_log["status"] == "failed":
            try:
                capture(page, screenshots_dir, run_log, "error")
            except Exception:
                pass
        if context is not None:
            try:
                if run_log.get("channel") != "bitbrowser":
                    context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if close_bit_profile and opened_bit_profile:
            _bitbrowser_close_profile(dict(payload.get("bitbrowser") or {}), opened_bit_profile)
        run_log["end_time"] = now_iso()
        write_json(run_log_path, run_log)
        result["screenshot_paths"] = run_log["screenshot_paths"]

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish WeChat OA content via Playwright")
    parser.add_argument("--input", required=True, help="Path to request JSON")
    parser.add_argument("--workspace", default=".", help="Workspace root (default: current dir)")
    parser.add_argument("--dry-run", action="store_true", help="Validate input and write run log only")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).resolve()
    workspace = Path(args.workspace).resolve()
    payload = load_json(input_path)
    selectors = load_selectors(payload)
    result = run_publish(payload, selectors, workspace, args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if str(result.get("status") or "") in {"success", "waiting_manual_publish", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
