"""Qwen-VL passport OCR over the brain.py httpx/DashScope transport pattern.

Returns a raw MRZ/field dict.  Makes NO trust decision — validate() is the
gate.  Image bytes are never persisted or logged.

Transport: pure httpx against DashScope's OpenAI-compatible endpoint (same
DASHSCOPE_API_KEY + base URL that brain.py uses).  Model id is
env-overridable (WAYBOT_VL_MODEL, default qwen-vl-max).  The transport is
injectable so tests never hit the network.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

# DashScope OpenAI-compatible endpoint — mirrors brain.py's pattern.
DASHSCOPE_URL = os.environ.get(
    "DASHSCOPE_BASE_URL",
    "https://ws-332gxo4yax9lutfc.ap-southeast-1.maas.aliyuncs.com"
    "/compatible-mode/v1/chat/completions",
)
DEFAULT_VL_MODEL = os.environ.get("WAYBOT_VL_MODEL", "qwen-vl-max")
EXTRACT_TIMEOUT = 30.0

# Injectable transport seam (same pattern as brain.py).
Transport = Callable[[list[dict]], Awaitable[str]]

# The extraction prompt — structured output, no instruction-following on
# the image content.  MRZ fields consumed as DATA only.
_EXTRACT_PROMPT = (
    "Extract the two MRZ lines from this passport photo.  Return ONLY "
    "a JSON object with keys 'mrz_line1' and 'mrz_line2', each a string "
    "of exactly 44 characters.  No prose, no markdown fences."
)


async def extract_passport(
    image_bytes: bytes,
    *,
    transport: Transport | None = None,
) -> dict:
    """Call Qwen-VL to extract MRZ lines from a passport photo.

    Returns a raw dict (expected keys: mrz_line1, mrz_line2).  The caller
    feeds this into mrz.validate() — this function makes NO trust decision.
    Image bytes are never persisted or logged.

    Raises on transport/parse failure — the caller catches and falls back
    to typed entry.
    """
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:image/jpeg;base64,{b64}"

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": _EXTRACT_PROMPT},
            ],
        }
    ]

    if transport is not None:
        raw = await transport(messages)
    else:
        api_key = os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY not set")
        async with httpx.AsyncClient(timeout=EXTRACT_TIMEOUT) as client:
            resp = await client.post(
                DASHSCOPE_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": DEFAULT_VL_MODEL,
                    "messages": messages,
                    "temperature": 0.1,
                },
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]

    # Defensive parse: strip markdown fences, extract JSON object.
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return json.loads(text)
