"""T6 Provider 账单格式定义。

P1-A T6 规格落地（153-T6-Design）：
    - 4 家 Provider 账单格式枚举
    - 字段映射规范
    - 与 cost_defense/model.py CostEvent 对齐

约束：仅标准库 · 零外部依赖
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Provider(str, Enum):
    """Provider 枚举（与 router_r3.py 第 112-133 行对齐）。"""

    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    TOKEN_PLAN = "token-plan"
    NOVAROUTEAI = "novarouteai"


class BillingFormat(str, Enum):
    """账单格式枚举。"""

    CSV = "csv"
    JSON = "json"


@dataclass
class BillingFieldMapping:
    """账单字段映射。"""

    source_field: str        # Provider 账单中的字段名
    target_field: str        # 映射到 CostEvent 的字段名
    transform: str = ""      # 转换函数（如 "cents_to_usd"）

    def to_dict(self) -> Dict[str, str]:
        return {
            "source_field": self.source_field,
            "target_field": self.target_field,
            "transform": self.transform,
        }


@dataclass
class BillingSchema:
    """Provider 账单格式定义。"""

    provider: str
    format: str
    required_fields: List[str]
    field_mappings: List[BillingFieldMapping]
    timestamp_format: str = "%Y-%m-%dT%H:%M:%S"
    notes: str = ""

    def validate(self) -> List[str]:
        """校验账单格式定义。"""
        errors = []
        if not self.provider:
            errors.append("provider 不得为空")
        if self.format not in {BillingFormat.CSV.value, BillingFormat.JSON.value}:
            errors.append(f"非法格式: {self.format!r}")
        if not self.required_fields:
            errors.append("required_fields 不得为空")
        if not self.field_mappings:
            errors.append("field_mappings 不得为空")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "format": self.format,
            "required_fields": self.required_fields,
            "field_mappings": [m.to_dict() for m in self.field_mappings],
            "timestamp_format": self.timestamp_format,
            "notes": self.notes,
        }


# ── 4 家 Provider 账单格式定义 ───────────────────────────────────────

DEEPSEEK_SCHEMA = BillingSchema(
    provider=Provider.DEEPSEEK.value,
    format=BillingFormat.CSV.value,
    required_fields=["timestamp", "model", "prompt_tokens", "completion_tokens", "cost_usd"],
    field_mappings=[
        BillingFieldMapping("timestamp", "ts"),
        BillingFieldMapping("model", "model"),
        BillingFieldMapping("prompt_tokens", "tokens_in"),
        BillingFieldMapping("completion_tokens", "tokens_out"),
        BillingFieldMapping("cost_usd", "cost_usd"),
    ],
    notes="DeepSeek CSV 可导出，字段与 CostEvent 直接对齐。",
)

QWEN_SCHEMA = BillingSchema(
    provider=Provider.QWEN.value,
    format=BillingFormat.CSV.value,
    required_fields=["账单时间", "模型", "输入Token", "输出Token", "费用"],
    field_mappings=[
        BillingFieldMapping("账单时间", "ts"),
        BillingFieldMapping("模型", "model"),
        BillingFieldMapping("输入Token", "tokens_in"),
        BillingFieldMapping("输出Token", "tokens_out"),
        BillingFieldMapping("费用", "cost_usd", transform="cny_to_usd"),
    ],
    notes="Qwen 阿里云账单中心 CSV，费用为 CNY 需换算 USD。待 Tristan 验证。",
)

TOKEN_PLAN_SCHEMA = BillingSchema(
    provider=Provider.TOKEN_PLAN.value,
    format=BillingFormat.CSV.value,
    required_fields=["时间", "Credits消耗", "模型"],
    field_mappings=[
        BillingFieldMapping("时间", "ts"),
        BillingFieldMapping("模型", "model"),
        BillingFieldMapping("Credits消耗", "cost_usd", transform="credits_to_usd"),
    ],
    notes="Token-Plan Credits 计费，需 USD 换算。待 Tristan 验证。",
)

NOVAROUTEAI_SCHEMA = BillingSchema(
    provider=Provider.NOVAROUTEAI.value,
    format=BillingFormat.JSON.value,
    required_fields=["timestamp", "model", "tokens_in", "tokens_out", "cost_usd"],
    field_mappings=[
        BillingFieldMapping("timestamp", "ts"),
        BillingFieldMapping("model", "model"),
        BillingFieldMapping("tokens_in", "tokens_in"),
        BillingFieldMapping("tokens_out", "tokens_out"),
        BillingFieldMapping("cost_usd", "cost_usd"),
    ],
    notes="NovaRouteAI JSON 遥测数据，字段全公开。待 Tristan 验证。",
)

# 4 家 Provider 账单格式注册表
BILLING_SCHEMAS: Dict[str, BillingSchema] = {
    Provider.DEEPSEEK.value: DEEPSEEK_SCHEMA,
    Provider.QWEN.value: QWEN_SCHEMA,
    Provider.TOKEN_PLAN.value: TOKEN_PLAN_SCHEMA,
    Provider.NOVAROUTEAI.value: NOVAROUTEAI_SCHEMA,
}


def get_billing_schema(provider: str) -> Optional[BillingSchema]:
    """获取 Provider 账单格式。"""
    return BILLING_SCHEMAS.get(provider)
