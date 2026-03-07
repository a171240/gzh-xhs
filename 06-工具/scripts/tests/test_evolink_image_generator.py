#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from evolink_image_generator import EvolinkImageGenerator


def test_generate_image_sync_converts_network_submit_error_to_retryable_failure(tmp_path: Path) -> None:
    generator = EvolinkImageGenerator(output_dir=str(tmp_path), api_key="test-key")
    generator.session = object()  # mark initialized
    generator.model_candidates = ["gemini-3.1-flash-image-preview"]

    def raise_ssl(*args, **kwargs):
        raise requests.exceptions.SSLError("ssl eof")

    generator._submit_generation = raise_ssl  # type: ignore[method-assign]

    result = generator._generate_image_sync("prompt", "gongchang", "cover", "21:9")

    assert result is None
    assert generator.last_error_code == "network_request_failed"
    assert "ssl eof" in str(generator.last_error or "")
