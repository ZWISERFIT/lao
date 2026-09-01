# -*- coding: utf-8 -*-
"""raw_trace.redaction · 脱敏钩子（写入路径上的强制一步）

口径：所有进入原卷账的载荷必须先过脱敏钩子；钩子纯本地、正则替换，
不依赖任何外部服务（零遥测边界）。
"""
from __future__ import annotations

import re
from typing import Any

# 纯哈希（32/40/64位hex）不脱敏：登记口径依赖哈希可对照（件②对齐）
_HEX_RE = re.compile(r"^[0-9a-fA-F]{32}$|^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$")
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")


def _redact_long_token(m: "re.Match") -> str:
    s = m.group(0)
    if _HEX_RE.match(s):
        return s  # 保留哈希原样，不误伤
    return "[REDACTED_LONG_TOKEN]"


# 脱敏规则：(正则, 替换)。仅做保守替换，宁可误伤不漏放。
_PATTERNS = [
    # 邮箱
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
     "[REDACTED_EMAIL]"),
    # 疑似 API key / token（长串，纯哈希除外）
    (_LONG_TOKEN_RE, _redact_long_token),
    # 中国大陆手机号（不用\b：中文字符也是word char，改用数字边界）
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[REDACTED_PHONE]"),
    # IPv4
    (re.compile(r"(?<![\d.])\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![\d.])"),
     "[REDACTED_IP]"),
]


def redact_text(text: str) -> str:
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


def redact_payload(payload: Any) -> Any:
    """对任意 JSON 载荷递归脱敏。返回新对象，不改原载荷。"""
    if isinstance(payload, str):
        return redact_text(payload)
    if isinstance(payload, dict):
        return {k: redact_payload(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [redact_payload(v) for v in payload]
    return payload
