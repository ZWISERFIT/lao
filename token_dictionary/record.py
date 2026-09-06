"""T5 Token 字段字典 — TokenRecord 数据类与 JSON Schema。

P1-A T5 规格落地（145-T5-Design）：
    - TokenRecord: 标准化 Token 记录（14 字段）
    - JSON Schema: 序列化/反序列化校验
    - Provider 枚举校验
    - 工厂方法: 从 router_r3.py 事件日志字段构造

约束：仅标准库 · 零外部依赖 · 全部合成数据验证
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

UTC = timezone.utc

# ── Provider 枚举（router_r3.py 第 112-133 行 PROVIDER_CONFIG 已核验） ──

VALID_PROVIDERS = frozenset({"deepseek", "qwen", "token-plan", "novarouteai"})

# ── JSON Schema ───────────────────────────────────────────────────────

TOKEN_RECORD_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "TokenRecord",
    "description": "LAO 标准化 Token 记录（P1-A T5 规格）",
    "type": "object",
    "required": ["request_id", "provider", "model", "input_tokens", "output_tokens"],
    "properties": {
        "request_id": {
            "type": "string",
            "description": "请求唯一标识",
        },
        "task_id": {
            "type": "string",
            "description": "任务 ID（来自 T2 TaskIdentity）",
        },
        "provider": {
            "type": "string",
            "enum": sorted(VALID_PROVIDERS),
            "description": "Provider 名（4 个已核验）",
        },
        "model": {
            "type": "string",
            "description": "实际使用的模型名",
        },
        "input_tokens": {
            "type": "integer",
            "minimum": 0,
            "description": "输入 token 数",
        },
        "output_tokens": {
            "type": "integer",
            "minimum": 0,
            "description": "输出 token 数",
        },
        "total_tokens": {
            "type": "integer",
            "description": "总 token 数（= input_tokens + output_tokens）",
        },
        "cache_hit_tokens": {
            "type": "integer",
            "minimum": 0,
            "description": "缓存命中 token 数",
        },
        "cache_miss_tokens": {
            "type": "integer",
            "minimum": 0,
            "description": "缓存未命中 token 数",
        },
        "cache_hit_rate": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "缓存命中率（= cache_hit / (cache_hit + cache_miss)）",
        },
        "cost_yuan": {
            "type": "number",
            "minimum": 0,
            "description": "成本（人民币元）",
        },
        "pricing_regime": {
            "type": "string",
            "description": "计价来源标注（official-csv / fallback / synthetic）",
        },
        "timestamp": {
            "type": "string",
            "format": "date-time",
            "description": "记录时间（UTC ISO-8601）",
        },
        "source": {
            "type": "string",
            "description": "数据来源（routing_cost_log / official_csv / synthetic）",
        },
    },
}


# ── 数据类 ────────────────────────────────────────────────────────────

@dataclass
class TokenRecord:
    """LAO 标准化 Token 记录。

    字段定义与 P1-A T5 规格书（145-T5-Design）完全对齐。
    """

    request_id: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    cache_hit_rate: Optional[float] = None
    cost_yuan: float = 0.0
    pricing_regime: str = ""
    task_id: str = ""
    timestamp: str = ""
    source: str = ""

    # ── 工厂方法 ──────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        request_id: str,
        provider: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_hit_tokens: int = 0,
        cache_miss_tokens: int = 0,
        cost_yuan: float = 0.0,
        pricing_regime: str = "",
        task_id: str = "",
        source: str = "",
    ) -> "TokenRecord":
        """创建标准化 TokenRecord。

        自动计算 total_tokens 和 cache_hit_rate。

        Args:
            request_id: 请求唯一标识。
            provider: Provider 名（必须为 4 个已核验之一）。
            model: 实际使用的模型名。
            input_tokens: 输入 token 数。
            output_tokens: 输出 token 数。
            cache_hit_tokens: 缓存命中 token 数。
            cache_miss_tokens: 缓存未命中 token 数。
            cost_yuan: 成本（人民币元）。
            pricing_regime: 计价来源标注。
            task_id: 任务 ID（来自 T2 TaskIdentity）。
            source: 数据来源。

        Returns:
            新建的 TokenRecord 实例。

        Raises:
            ValueError: provider 不在已核验枚举中，或 token 数为负。
        """
        if provider not in VALID_PROVIDERS:
            raise ValueError(
                f"provider 必须为 {sorted(VALID_PROVIDERS)} 之一，实际: {provider!r}"
            )
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("input_tokens 和 output_tokens 不得为负")
        if cache_hit_tokens < 0 or cache_miss_tokens < 0:
            raise ValueError("cache_hit_tokens 和 cache_miss_tokens 不得为负")

        total = input_tokens + output_tokens
        hit_rate = compute_hit_rate(cache_hit_tokens, cache_miss_tokens)
        now = datetime.now(UTC).isoformat()

        return cls(
            request_id=request_id,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            cache_hit_rate=hit_rate,
            cost_yuan=cost_yuan,
            pricing_regime=pricing_regime,
            task_id=task_id,
            timestamp=now,
            source=source,
        )

    # ── 校验 ──────────────────────────────────────────────────────

    def validate(self) -> List[str]:
        """校验所有字段格式，返回错误列表（空列表=通过）。"""
        errors: List[str] = []
        if not self.request_id:
            errors.append("request_id 不得为空")
        if self.provider not in VALID_PROVIDERS:
            errors.append(f"provider 非法: {self.provider!r}")
        if not self.model:
            errors.append("model 不得为空")
        if self.input_tokens < 0:
            errors.append("input_tokens 不得为负")
        if self.output_tokens < 0:
            errors.append("output_tokens 不得为负")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            errors.append(
                f"total_tokens({self.total_tokens}) "
                f"!= input({self.input_tokens}) + output({self.output_tokens})"
            )
        if self.cache_hit_tokens < 0:
            errors.append("cache_hit_tokens 不得为负")
        if self.cache_miss_tokens < 0:
            errors.append("cache_miss_tokens 不得为负")
        if self.cache_hit_rate is not None:
            if not (0 <= self.cache_hit_rate <= 1):
                errors.append(f"cache_hit_rate 超出 [0,1]: {self.cache_hit_rate}")
        if self.cost_yuan < 0:
            errors.append("cost_yuan 不得为负")
        return errors

    # ── 序列化 ────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典。"""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """序列化为 JSON 字符串。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TokenRecord":
        """从字典反序列化。"""
        return cls(
            request_id=data["request_id"],
            provider=data["provider"],
            model=data["model"],
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            total_tokens=data.get("total_tokens", 0),
            cache_hit_tokens=data.get("cache_hit_tokens", 0),
            cache_miss_tokens=data.get("cache_miss_tokens", 0),
            cache_hit_rate=data.get("cache_hit_rate"),
            cost_yuan=data.get("cost_yuan", 0.0),
            pricing_regime=data.get("pricing_regime", ""),
            task_id=data.get("task_id", ""),
            timestamp=data.get("timestamp", ""),
            source=data.get("source", ""),
        )


# ── 命中率计算（标准函数） ────────────────────────────────────────────

def compute_hit_rate(cache_hit: int, cache_miss: int) -> Optional[float]:
    """计算缓存命中率。

    公式：cache_hit / (cache_hit + cache_miss)
    与 router_r3.py 第 380-391 行 _provider_cache_hit_rate() 公式一致。

    Args:
        cache_hit: 缓存命中 token 数。
        cache_miss: 缓存未命中 token 数。

    Returns:
        命中率 float [0, 1]；分母为 0 时返回 None。
    """
    total = cache_hit + cache_miss
    if total == 0:
        return None
    return cache_hit / total
