"""ExperienceReplay — 经验直返引擎 (207号件·加固一)

路由前查询可复用资产 → 命中则直接返回 → 不进 LLM。

这是 LAO 降本的核心链路：
  请求 → ExperienceReplay.query(task_text)
       → 命中 → 直接构造 OpenAI 兼容响应（0 token 花费）
       → 未命中 → 继续走 LLM 路由

与 W3 经验直答的区别：
  - W3 依赖 ExperienceLoop.match_experience()（锚点库 + 认知匹配 + 双重确认）
  - ExperienceReplay 依赖 ReusableAssetStore（更轻量的快速检索层）
  - 两者互补：W3 是确权经验直答，Replay 是可复用资产直返
  - Replay 查询优先级更高（更快、更宽松），W3 更严格（双重确认）

查询顺序（chat_completions 内）：
  1. ExperienceReplay.query_best() → 命中 → 直接返回
  2. LOOP.match_experience() → 高置信 + 认知匹配 → 短路返回
  3. 正常 LLM 路由
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from lao.effect_anchored.routing.reusable_asset import ReusableAssetStore


DEFAULT_REPLAY_LOG_PATH = os.environ.get(
    "LAO_REPLAY_LOG_PATH",
    os.path.join(os.path.expanduser("~"), ".lao", "experience_replay_log.jsonl"),
)


class ExperienceReplayEngine:
    """经验直返引擎：路由前查询可复用资产，命中则直接返回。"""

    def __init__(self, asset_store: Optional[ReusableAssetStore] = None,
                 min_confidence: float = 0.7,
                 replay_log_path: Optional[str] = None):
        self.store = asset_store or ReusableAssetStore()
        self.min_confidence = min_confidence
        self.replay_log_path = replay_log_path or DEFAULT_REPLAY_LOG_PATH
        self._stats = {"queries": 0, "hits": 0, "misses": 0}

    def query(self, task_text: str, tier: str = "", agent: str = ""
              ) -> Optional[Dict[str, Any]]:
        """查询可复用资产，返回最佳匹配（None = 未命中）。

        Args:
            task_text: 用户请求文本
            tier: 任务分层
            agent: Agent 标识

        Returns:
            {
                "answer": str,         # 直返答案
                "confidence": float,   # 置信度
                "asset_id": str,       # 命中的资产 ID
                "source": "experience_replay",
                "category": str,       # 资产类别
            }
            或 None（未命中）
        """
        self._stats["queries"] += 1

        if not task_text or len(task_text.strip()) < 5:
            self._stats["misses"] += 1
            return None

        match = self.store.query_best(task_text, min_confidence=self.min_confidence)
        if match is None:
            self._stats["misses"] += 1
            return None

        # 命中
        self._stats["hits"] += 1
        asset_id = match.get("asset_id", "")
        # 标记使用
        self.store.mark_used(asset_id)
        # 记录日志
        self._log_replay(task_text, tier, agent, match)

        return {
            "answer": match.get("solution", ""),
            "confidence": match.get("confidence", 0),
            "asset_id": asset_id,
            "source": "experience_replay",
            "category": match.get("category", ""),
            "pattern": match.get("pattern", "")[:200],
        }

    def build_response(self, replay_result: Dict[str, Any],
                       model_hint: str = "", request_id: str = ""
                       ) -> Dict[str, Any]:
        """将直返结果构造为 OpenAI 兼容响应。"""
        answer = replay_result.get("answer", "")
        if not answer:
            return {}
        return {
            "id": f"chatcmpl-{request_id or uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_hint or "lao-experience-replay",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            "lao_validation": {
                "source": replay_result.get("source", "experience_replay"),
                "asset_id": replay_result.get("asset_id", ""),
                "confidence": replay_result.get("confidence", 0),
                "category": replay_result.get("category", ""),
            },
        }

    def _log_replay(self, task_text: str, tier: str, agent: str,
                    match: Dict[str, Any]) -> None:
        """记录直返事件（审计 + 可观测）。"""
        try:
            os.makedirs(os.path.dirname(self.replay_log_path) or ".", exist_ok=True)
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "task_text_preview": task_text[:200],
                "tier": tier,
                "agent": agent or "unknown",
                "asset_id": match.get("asset_id", ""),
                "category": match.get("category", ""),
                "confidence": match.get("confidence", 0),
                "cost_saved": "100%",
            }
            with open(self.replay_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # 日志记录失败不阻塞

    def stats(self) -> Dict[str, Any]:
        """引擎统计。"""
        total = self._stats["queries"]
        hits = self._stats["hits"]
        return {
            **self._stats,
            "hit_rate": round(hits / total, 4) if total > 0 else None,
            "asset_store_stats": self.store.stats(),
        }
