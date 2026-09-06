"""T6 日常成本对账流程模块。

P1-A T6 规格落地（153-T6-Design）：
    - 4 家 Provider 账单格式定义
    - 对账引擎（预期 vs 实际比对）
    - 差异报告生成

导出核心接口，供集成时 import。
"""

from .billing_schema import (
    Provider,
    BillingFormat,
    BillingFieldMapping,
    BillingSchema,
    DEEPSEEK_SCHEMA,
    QWEN_SCHEMA,
    TOKEN_PLAN_SCHEMA,
    NOVAROUTEAI_SCHEMA,
    BILLING_SCHEMAS,
    get_billing_schema,
)
from .reconciler import (
    CostRecord,
    AggregatedCost,
    ReconciliationConfig,
    Reconciler,
)
from .discrepancy import (
    DiscrepancyItem,
    DiscrepancyReport,
)

__all__ = [
    # billing_schema
    "Provider",
    "BillingFormat",
    "BillingFieldMapping",
    "BillingSchema",
    "DEEPSEEK_SCHEMA",
    "QWEN_SCHEMA",
    "TOKEN_PLAN_SCHEMA",
    "NOVAROUTEAI_SCHEMA",
    "BILLING_SCHEMAS",
    "get_billing_schema",
    # reconciler
    "CostRecord",
    "AggregatedCost",
    "ReconciliationConfig",
    "Reconciler",
    # discrepancy
    "DiscrepancyItem",
    "DiscrepancyReport",
]
