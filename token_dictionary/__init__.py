"""T5 Token 字段字典与 Provider 级命中率模块。

P1-A T5 规格落地（145-T5-Design）：
    - TokenRecord: 标准化 Token 记录
    - ProviderStats: Provider 级命中率统计
    - ProviderStatsRegistry: 多 Provider 统计注册表
    - compute_hit_rate: 标准命中率计算函数

导出核心接口，供 router_r3.py 集成时 import。
"""

from .record import (
    TokenRecord,
    TOKEN_RECORD_SCHEMA,
    VALID_PROVIDERS,
    compute_hit_rate,
)
from .provider_stats import (
    ProviderStats,
    ProviderStatsRegistry,
    DEFAULT_WINDOW_SIZE,
    MIN_SAMPLES,
)

__all__ = [
    "TokenRecord",
    "TOKEN_RECORD_SCHEMA",
    "VALID_PROVIDERS",
    "compute_hit_rate",
    "ProviderStats",
    "ProviderStatsRegistry",
    "DEFAULT_WINDOW_SIZE",
    "MIN_SAMPLES",
]
