"""N1 指标落盘（180号 §4.7 指标与可观测）。

每条请求一行 JSONL，字段严格按 180号 §4.7 表；追加两个语境字段（context / adapter）
以便 OpenClaw 与 DSH 两条线的数据不被混算（195号 §3.2「两者不可混算」）。

写入策略：追加写 + 失败静默。指标永不阻塞请求（fail-open 与核心引擎一致）。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Optional

from .engine import CompressionDecision

_WRITE_LOCK = threading.Lock()


def build_record(
    decision: CompressionDecision,
    *,
    context: str,
    adapter: str,
    chars_before: int = 0,
    chars_after: Optional[int] = None,
    agent: str = "",
    request_id: str = "",
) -> Dict[str, Any]:
    """组装一条指标记录（180号 §4.7 字段表）。

    Args:
        decision: 引擎决策。
        context: 语境（openclaw / dsh）。
        adapter: 适配器标识，便于同语境多落点区分。
        chars_before: 裁剪前 tools 字段字符数（由适配器实测）。
        chars_after: 裁剪后字符数；None 表示本语境测不到（DSH 的 restrict() 在插件侧
            执行，Python 这边只出名单、拿不到裁后字符数）。此时压缩率退回按工具条数
            计算并把 chars_after 落成 null，而不是当成 0 —— 否则 (before-0)/before
            会产出虚假的 100%，把 §4.7 的「压缩率<40% 告警」永久糊住。
        agent: 归因 agent。
        request_id: 请求 id。

    Returns:
        可直接 json.dumps 的字典。
    """
    measured = chars_before > 0 and isinstance(chars_after, int)
    if measured:
        ratio = (chars_before - chars_after) / chars_before
    elif decision.original_count > 0:
        ratio = decision.ratio
    else:
        ratio = 0.0
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "module": "n1_cognitive_compression",
        "context": context,
        "adapter": adapter,
        "agent": agent,
        "request_id": request_id,
        "intent": decision.intent,
        "tools_before": decision.original_count,
        "tools_after": decision.kept_count,
        "chars_before": chars_before,
        "chars_after": chars_after if measured else None,
        "compression_ratio": round(ratio, 4),
        "ratio_basis": "chars" if measured else "tools",
        "cache_hit": decision.cache_hit,
        "latency_ms": round(decision.latency_ms, 3),
        "fallback": decision.fallback,
        "dropped_tools": list(decision.dropped),
    }


def emit(path: str, record: Dict[str, Any]) -> bool:
    """追加一行 JSONL。

    Args:
        path: 目标文件；空字符串表示不落盘。
        record: build_record 的产物。

    Returns:
        True = 已写入；False = 未写入（未配置路径或写失败）。
    """
    if not path:
        return False
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with _WRITE_LOCK:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return True
    except Exception:
        return False


def record_decision(
    path: str,
    decision: CompressionDecision,
    *,
    context: str,
    adapter: str,
    chars_before: int = 0,
    chars_after: Optional[int] = None,
    agent: str = "",
    request_id: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """组装并落盘，返回记录本体（便于调用方顺带写自己的事件日志）。"""
    rec = build_record(
        decision,
        context=context,
        adapter=adapter,
        chars_before=chars_before,
        chars_after=chars_after,
        agent=agent,
        request_id=request_id,
    )
    if extra:
        rec.update(extra)
    emit(path, rec)
    return rec
