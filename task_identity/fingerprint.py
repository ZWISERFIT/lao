"""T2 会话指纹升级：SHA1-16位 → SHA-256-64位。

P1-A T2 规格落地（143-T2-Design 第六节）：
    - 算法升级：SHA1 → SHA-256（碰撞抗性 160→256 位）
    - 长度升级：16 位 hex → 64 位 hex
    - 输入逻辑不变：首条 system + 首条 user 消息前 512 字符，\\x1f 分隔
    - 向后兼容：旧 16 位指纹自然过期（会话粘性 TTL=6h）

约束：仅标准库 · 零外部依赖
"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, List


def compute_session_fingerprint(messages: List[Dict]) -> str:
    """计算会话指纹（SHA-256 全文 hex，64 位小写十六进制）。

    算法：取 messages 中首条 role=system 和首条 role=user 的 content
    前 512 字符，以 \\x1f（Unit Separator）拼接，计算 SHA-256 全文。

    与 router_r3.py 第 319-332 行 _session_fingerprint() 的输入逻辑完全一致，
    仅将 SHA1[:16] 升级为 SHA-256 全文。

    Args:
        messages: OpenAI 兼容的 messages 数组。

    Returns:
        64 位小写 hex 字符串。若 messages 为空或无 system/user，
        则对空字符串计算 SHA-256（仍返回合法 64 位 hex）。

    合成验证说明：
        本函数仅处理传入的 messages 参数，不涉及任何真实数据。
    """
    parts: List[str] = []
    for want in ("system", "user"):
        for m in messages:
            if isinstance(m, dict) and m.get("role") == want:
                c = m.get("content", "")
                if not isinstance(c, str):
                    c = json.dumps(c, ensure_ascii=False, default=str)
                parts.append(c[:512])
                break
    raw = "\x1f".join(parts)
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()
