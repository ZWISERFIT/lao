"""
Ethan Experience Evaluator — LAO v3.1 P0-2
============================================

Ethan 评估引擎客户端：调本地 Ethan `/attest/evaluate` 做三维度估值。

三维度(独特/验证/需求) + 区间参考 + 稀有度，返回供第④授权(确权交易)决策。

边界:
  - Ethan 是本地存证 + 评估服务(port 17800, 与 lao 同机)
  - 评估输入只含哈希化经验元数据(不含原始数据) → 隐私友好
  - 评分用于「是否值得确权上链」的可信参考, 不做 Melody 的个性化推断
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Any, Dict, Optional


EVALUATE_URL = os.environ.get(
    "ETHAN_EVALUATE_URL", "http://localhost:17800/attest/evaluate"
)


class ExperienceEvaluator:
    """Ethan 评估引擎客户端。

    evaluate(rule, domain, trust_weight, ...) → {dimensions, overall, band, rarity}
    """

    def evaluate(
        self,
        content_hash: str,
        rule: str = "",
        domain: str = "",
        trust_weight: float = 0.5,
        evidence_count: int = 1,
        relevance: float = 0.5,
        content_type: str = "experience",
    ) -> Optional[Dict[str, Any]]:
        """三维度评估一条经验。

        Args:
            content_hash: 经验内容哈希
            rule: 经验规则/内容(仅元数据, 非原始数据)
            domain: 所属领域
            trust_weight: 信任度 0-1
            evidence_count: 证据数
            relevance: 需求相关性 0-1

        Returns:
            {dimensions, overall, band, rarity, reference_range, evaluation_id}
            或 None(Ethan 不可达/失败)
        """
        body = json.dumps({
            "content_hash": content_hash,
            "content_type": content_type,
            "owner": "lao-client",
            "metadata": {
                "rule": rule,
                "domain": domain,
                "trust_weight": trust_weight,
                "evidence_count": evidence_count,
                "relevance": relevance,
            },
        }).encode("utf-8")
        req = urllib.request.Request(
            EVALUATE_URL, data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status != 200:
                    return None
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return None

    def is_worth_trading(self, result: Optional[Dict[str, Any]],
                         min_overall: float = 0.6) -> bool:
        """评估分是否达到「值得确权交易」门槛(供④决策参考)。"""
        if not result:
            return False
        return float(result.get("overall", 0.0) or 0.0) >= min_overall
