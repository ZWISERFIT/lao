"""
L1 路由决策层 — 31 模型自主选择
================================

功耗感知的任务分类与模型路由，根据任务难度选择最合适的模型，
同时追踪成本。

Classes:
    TaskClassifier — 任务关键词 → 难度层级
    ModelRouter   — 难度层级 → 模型 + 降级链路
    CostTracker   — 调用成本日志与汇总
"""

from .task_classifier import TaskClassifier
from .model_router import ModelRouter, RouteSelection
from .cost_tracker import CostTracker

__all__ = [
    "TaskClassifier",
    "ModelRouter",
    "RouteSelection",
    "CostTracker",
]
