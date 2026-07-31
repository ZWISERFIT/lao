"""
约束代码生成器 — LAO v2 经验复利层
=====================================

核心能力：从经验中自动生成约束规则，让犯过的错不会再犯。

创始人说："你遇到的任何问题，最终都会沉淀为一条约束规则"
这意味着：每次BMC发现异常模式 → 自动生成约束 → 写入规则注册表 → 下次同样场景触发前挡截。

三种约束生成方式：
1. 行为模式 → 约束（BMC检测到高概率沉默 → 生成"沉默预警"约束）
2. LLM检测 → 约束（H-function挡截后 → 生成新锚点约束）
3. 人工确认 → 约束（创始人/Zeus确认某条规则）
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Callable
from enum import Enum
import json
import hashlib
import re


class ConstraintLevel(str, Enum):
    """约束级别（对应宪法三色）"""
    RED = "red"        # 🔴 必须遵守，违反=违规
    YELLOW = "yellow"  # 🟡 建议遵守，违反=警告
    GREEN = "green"    # 🟢 参考可选


class ConstraintDomain(str, Enum):
    """约束作用域"""
    BEHAVIOR = "behavior"     # 用户行为相关
    SYSTEM = "system"         # 系统/运维相关
    OUTPUT = "output"         # 产出内容相关
    COORDINATION = "coordination"  # Agent协同相关
    DECISION = "decision"     # 决策相关


@dataclass
class Constraint:
    """
    一条可执行的约束规则
    
    不是"知识文档"，是"可执行的规则文件"
    Ferrum Gate可以直接执行的约束：
    - 规则文本 → 人类可读
    - exec_script → 机器可执行
    - 自动触发条件 → 条件匹配时自动执行
    """
    id: str
    domain: ConstraintDomain
    level: ConstraintLevel
    
    # 规则本身
    rule: str  # 人类可读的规则描述
    
    # 触发条件
    trigger_pattern: str  # 匹配表达式（正则/关键词）
    
    # 执行
    exec_script: Optional[str] = None  # 自动执行脚本（可选）
    
    # 元数据
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source_event: Optional[str] = None  # 来源事件ID
    source_agent: Optional[str] = "tristan"
    active: bool = True
    hit_count: int = 0
    last_hit_at: Optional[datetime] = None
    
    def __post_init__(self):
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
        if isinstance(self.last_hit_at, str):
            self.last_hit_at = datetime.fromisoformat(self.last_hit_at)
    
    def matches(self, context: str) -> bool:
        """检查上下文是否触发此约束"""
        if not re.search(self.trigger_pattern, context, re.IGNORECASE):
            return False
        self.hit_count += 1
        self.last_hit_at = datetime.now(timezone.utc)
        return True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "domain": self.domain.value,
            "level": self.level.value,
            "rule": self.rule,
            "trigger_pattern": self.trigger_pattern,
            "exec_script": self.exec_script,
            "created_at": self.created_at.isoformat(),
            "source_event": self.source_event,
            "source_agent": self.source_agent,
            "active": self.active,
            "hit_count": self.hit_count,
        }


class ConstraintGenerator:
    """
    约束代码生成器
    
    从经验/行为模式 → 自动生成可执行约束
    
    三种生成模式：
    - from_pattern: 行为模式→约束
    - from_violation: 违规行为→约束
    - from_founder: 创始人指令→约束
    """
    
    def __init__(self, rule_registry=None):
        self.rule_registry = rule_registry
        self.generated_count = 0
    
    def from_behavior_pattern(
        self,
        pattern_name: str,
        description: str,
        trigger_pattern: str,
        level: ConstraintLevel = ConstraintLevel.YELLOW,
        domain: ConstraintDomain = ConstraintDomain.BEHAVIOR,
    ) -> Constraint:
        """
        从行为模式生成约束
        
        例：BMC发现多个用户 Silent→Cancel → 生成流失预警约束
        
        Args:
            pattern_name: 模式名（用于生成约束ID）
            description: 规则描述
            trigger_pattern: 触发匹配表达式
            level: 约束级别
            domain: 作用域
        
        Returns:
            生成的Constraint
        """
        self.generated_count += 1
        ts = int(datetime.now(timezone.utc).timestamp())
        
        c = Constraint(
            id=f"C-BMC-{self.generated_count:03d}-{ts}",
            domain=domain,
            level=level,
            rule=f"[行为模式] {description}",
            trigger_pattern=trigger_pattern,
            source_event=f"bmc_pattern::{pattern_name}",
        )
        
        if self.rule_registry:
            self.rule_registry.register(c)
        
        return c
    
    def from_violation(
        self,
        violation_text: str,
        anchor_violated: str,
        level: ConstraintLevel = ConstraintLevel.RED,
    ) -> Constraint:
        """
        从违规事件生成约束
        
        例：H-function挡截了虚构产出 → 生成那条虚构模式的约束
        
        Args:
            violation_text: 违规描述
            anchor_violated: 被违反的锚点
            level: 约束级别（违规通常是🔴）
        """
        self.generated_count += 1
        ts = int(datetime.now(timezone.utc).timestamp())
        
        # 从违规文本生成trigger_pattern
        trigger_pattern = self._extract_keywords(violation_text)
        
        c = Constraint(
            id=f"C-VIOLATION-{self.generated_count:03d}-{ts}",
            domain=ConstraintDomain.OUTPUT,
            level=level,
            rule=f"⚠️ 防止同构违规: {violation_text[:100]}",
            trigger_pattern=trigger_pattern,
            source_event=f"h_function_violation::{anchor_violated}",
        )
        
        if self.rule_registry:
            self.rule_registry.register(c)
        
        return c
    
    def from_founder_instruction(
        self,
        instruction: str,
        trigger_pattern: str,
        level: ConstraintLevel = ConstraintLevel.RED,
    ) -> Constraint:
        """
        从创始人指令生成约束
        
        例：创始人说"以后fallback不能走全局key" → 生成约束
        
        Args:
            instruction: 创始人指令原文
            trigger_pattern: 触发条件
            level: 约束级别
        """
        self.generated_count += 1
        ts = int(datetime.now(timezone.utc).timestamp())
        
        c = Constraint(
            id=f"C-FOUNDER-{self.generated_count:03d}-{ts}",
            domain=ConstraintDomain.DECISION,
            level=level,
            rule=f"👑 创始人指令: {instruction[:150]}",
            trigger_pattern=trigger_pattern,
            source_event="founder_instruction",
        )
        
        if self.rule_registry:
            self.rule_registry.register(c)
        
        return c
    
    def from_failure(
        self,
        error: str,
        fix: str,
        context: str,
        level: ConstraintLevel = ConstraintLevel.RED,
    ) -> Constraint:
        """
        从故障修复经验生成约束 — RetroOnto核心逻辑
        
        例：portproxy规则导致全网断连 → 生成"操作Windows代理前检查portproxy"约束
        
        Args:
            error: 错误描述
            fix: 修复方法
            context: 发生场景
            level: 约束级别
        """
        self.generated_count += 1
        ts = int(datetime.now(timezone.utc).timestamp())
        
        c = Constraint(
            id=f"C-ERROR-{self.generated_count:03d}-{ts}",
            domain=ConstraintDomain.SYSTEM,
            level=level,
            rule=f"🛠 经验复利: {error}\n    修复: {fix}",
            trigger_pattern=self._extract_keywords(f"{error} {context}"),
            source_event=f"error_fix::{error[:50]}",
        )
        
        if self.rule_registry:
            self.rule_registry.register(c)
        
        return c
    
    def _extract_keywords(self, text: str) -> str:
        """从文本中提取关键词作为trigger_pattern"""
        # 提取中文词（2-6个汉字）和英文词
        words = re.findall(r'[\u4e00-\u9fff]{2,6}|[a-zA-Z_]{3,}', text)
        # 取前5个作为pattern
        keywords = words[:5]
        if not keywords:
            return f".*{text[:20]}.*"
        return '|'.join(keywords)
    
    def batch_from_behavior_patterns(
        self,
        patterns: List[Dict[str, Any]],
    ) -> List[Constraint]:
        """批量从行为模式生成约束"""
        return [self.from_behavior_pattern(**p) for p in patterns]


# 预定义行为模式（BMC引擎自动触发）
DEFAULT_BEHAVIOR_PATTERNS = [
    {
        "pattern_name": "churn_risk_high",
        "description": "当用户连3个沉默token → 触发流失预警",
        "trigger_pattern": "沉默|silence|没来|缺席|cancel",
        "level": ConstraintLevel.YELLOW,
        "domain": ConstraintDomain.BEHAVIOR,
    },
    {
        "pattern_name": "intention_expired",
        "description": "用户承诺概率<阈值 → 标记为已过期、通知跟进",
        "trigger_pattern": "续费|续约|会来|一定来|续费一年",
        "level": ConstraintLevel.GREEN,
        "domain": ConstraintDomain.BEHAVIOR,
    },
    {
        "pattern_name": "negative_dialog_spike",
        "description": "用户负面情绪连续出现 → 触发关怀流程",
        "trigger_pattern": "投诉|不满|太忙|没时间|取消|退费|太贵",
        "level": ConstraintLevel.YELLOW,
        "domain": ConstraintDomain.BEHAVIOR,
    },
]
