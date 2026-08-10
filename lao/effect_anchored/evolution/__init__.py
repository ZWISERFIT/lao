"""
L3 Evolution Module — 经验复利闭环
====================================

经验萃取 → 约束生成 → 规则注册 → 永久化 → 自动执行

Core engines:
    ExperienceExtractor  — 从 H 函数拦截事件自动萃取错误模式
    ConstraintGenerator — 从错误模式生成可执行 Python 约束代码
    RuleRegistry        — 规则版本谱系管理、冲突检测、废弃标记

Design principle:
    Every H-function failure leaves a trace that becomes a permanent constraint.
    The system gets smarter after every violation — not just flags errors,
    but generates executable Python code to prevent recurrence.
"""

__all__ = [
    "ExperienceExtractor",
    "ErrorPattern",
    "ExtractionInput",
    "ConstraintGenerator",
    "RuleRegistry",
    "ExperienceAtomEngine",
    "ExperienceAtom",
]

from .experience_extractor import ExperienceExtractor, ErrorPattern, ExtractionInput
from .constraint_generator import ConstraintGenerator
from .rule_registry import RuleRegistry
from .atom_engine import ExperienceAtomEngine, ExperienceAtom
