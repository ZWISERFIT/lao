"""T9 匿名评测集与 A/B 评测模块。

P1-A T9 规格落地（155-T9-Design）：
    - 30+ 合成评测样本
    - A/B 评测引擎（基线 vs 优化）
    - 评测报告生成

导出核心接口，供集成时 import。
"""

from .dataset import (
    EvalItem,
    EvalDataset,
    generate_synthetic_dataset,
    DEFAULT_EVAL_DATASET,
)
from .ab_engine import (
    EvalResult,
    RoundResult,
    ABTestResult,
    ABEngine,
)
from .report import EvalReport

__all__ = [
    # dataset
    "EvalItem",
    "EvalDataset",
    "generate_synthetic_dataset",
    "DEFAULT_EVAL_DATASET",
    # ab_engine
    "EvalResult",
    "RoundResult",
    "ABTestResult",
    "ABEngine",
    # report
    "EvalReport",
]
