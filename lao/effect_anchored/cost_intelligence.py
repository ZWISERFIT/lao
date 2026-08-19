# v3.5.2-laorefactor: P1
"""
Cost Intelligence — LAO 架构重构 P1
====================================

成本智能（产品规格 v1.0）:
    - 1.1 Step4  Token 合理性 (check_token_efficiency): 能用 Flash 别用 Pro
    - 1.2 Step3  成本核算 (settle_and_log): 记录命中/未命中·实际 token·花费
    - Zeus LAO-C2 期望成本路由 (expected_cost): 预测本次调用成本

约束: 仅标准库 · docstring · fail-open（任何异常返回 ok/0.0 不抛）。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional


class CostIntelligence:
    """成本智能（Token 合理性 + 成本核算 + 期望成本路由）。"""

    MODEL_COST_PER_1K = {        # 元/1K tokens（估算·可配）
        "pro": 0.003,
        "flash": 0.001,
    }
    CONTEXT_TOKEN_WARN = 8000    # context 超此阈值 → 砍冗余警告

    # 简单任务类型（可用 Flash·不需要 Pro）
    SIMPLE_TASK_TYPES = ("chat", "simple", "greeting", "summary")

    def __init__(self, cost_log_path: Optional[str] = None):
        """初始化成本智能。cost_log_path 缺省不落盘（仅内存统计）。"""
        self.cost_log_path = cost_log_path
        self._settled: List[Dict[str, Any]] = []
        self._total_cost = 0.0
        self._cache_hits = 0
        self._total_calls = 0

    # -- Step4: Token 合理性 -------------------------------------------------

    def check_token_efficiency(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Step4 Token 合理性: 能用 Flash 别用 Pro。

        Args:
            request: 含 request_features{task_type, model_used, context_tokens}。

        Returns:
            {"status": "ok"/"warning", "detail": str}

        fail-open: 异常返回 {"status": "ok", "detail": "fail-open"}。
        """
        try:
            feat = request.get("request_features") or {}
            task_type = str(feat.get("task_type") or "chat").lower()
            model = str(feat.get("model_used") or "").lower()
            ctx = int(feat.get("context_tokens", 0) or 0)

            warnings = []
            is_pro = "pro" in model
            if is_pro and task_type in self.SIMPLE_TASK_TYPES:
                warnings.append(
                    f"pro model for simple task '{task_type}' → suggest flash"
                )
            if ctx > self.CONTEXT_TOKEN_WARN:
                warnings.append(
                    f"context_tokens={ctx} > {self.CONTEXT_TOKEN_WARN} → trim redundant context"
                )
            if warnings:
                return {"status": "warning", "detail": " | ".join(warnings)}
            return {"status": "ok", "detail": "token usage reasonable"}
        except Exception:
            return {"status": "ok", "detail": "fail-open"}

    # -- Step3: 成本核算 ------------------------------------------------------

    def settle_and_log(self, request: Dict[str, Any],
                       response: Dict[str, Any]) -> Dict[str, Any]:
        """Step3 成本核算: 记录命中/未命中·实际 token·实际花费·更新统计。

        Args:
            request: 含 request_features{model_used, context_tokens}。
            response: 含 response_features{response_tokens, cache_hit, actual_cost}。

        Returns:
            {"status": "ok", "cost": float, "cache_hit": bool,
             "context_tokens": int, "response_tokens": int, "total_cost": float}

        fail-open: 异常返回 {"status": "ok", "cost": 0.0, ...}。
        """
        try:
            feat = request.get("request_features") or {}
            rfeat = response.get("response_features") or {}
            model = str(feat.get("model_used") or "").lower()
            ctx = int(feat.get("context_tokens", 0) or 0)
            resp_tok = int(rfeat.get("response_tokens", 0) or 0)
            cache_hit = bool(rfeat.get("cache_hit", False))
            actual_cost = float(rfeat.get("actual_cost", 0.0) or 0.0)

            # 未给实际花费 → 按模型费率估算
            if actual_cost <= 0:
                rate = self.MODEL_COST_PER_1K.get(
                    "flash" if "flash" in model else "pro", 0.001)
                actual_cost = (ctx + resp_tok) / 1000.0 * rate

            self._settled.append({
                "model": model, "context_tokens": ctx,
                "response_tokens": resp_tok, "cache_hit": cache_hit,
                "cost": actual_cost,
            })
            self._total_cost += actual_cost
            self._total_calls += 1
            if cache_hit:
                self._cache_hits += 1

            if self.cost_log_path:
                try:
                    os.makedirs(os.path.dirname(self.cost_log_path), exist_ok=True)
                    with open(self.cost_log_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(self._settled[-1], ensure_ascii=False) + "\n")
                except Exception:
                    pass

            return {
                "status": "ok",
                "cost": round(actual_cost, 6),
                "cache_hit": cache_hit,
                "context_tokens": ctx,
                "response_tokens": resp_tok,
                "total_cost": round(self._total_cost, 6),
            }
        except Exception:
            return {"status": "ok", "cost": 0.0, "cache_hit": False,
                    "context_tokens": 0, "response_tokens": 0, "total_cost": 0.0}

    # -- Zeus LAO-C2: 期望成本路由 -------------------------------------------

    def expected_cost(self, request: Dict[str, Any]) -> float:
        """Zeus LAO-C2 期望成本路由: 预测本次调用成本（元）。

        expected = (context_tokens + response_tokens) / 1000 * rate
        response_tokens 未提供时按 context 的 20% 估算。

        fail-open: 返回 0.0。
        """
        try:
            feat = request.get("request_features") or {}
            rfeat = request.get("response_features") or {}
            model = str(feat.get("model_used") or "").lower()
            ctx = int(feat.get("context_tokens", 0) or 0)
            resp_tok = int(rfeat.get("response_tokens", 0) or 0)
            if resp_tok <= 0:
                resp_tok = int(ctx * 0.2)
            rate = self.MODEL_COST_PER_1K.get(
                "flash" if "flash" in model else "pro", 0.001)
            return round((ctx + resp_tok) / 1000.0 * rate, 6)
        except Exception:
            return 0.0

    # -- 统计 ---------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """当前成本统计。"""
        return {
            "total_calls": self._total_calls,
            "total_cost": round(self._total_cost, 6),
            "cache_hits": self._cache_hits,
            "cache_hit_rate": round(
                self._cache_hits / self._total_calls, 4) if self._total_calls else 0.0,
        }
