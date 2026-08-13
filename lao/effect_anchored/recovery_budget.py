"""
RecoveryBudget — Recovery Budget + Safe Mode + Human Approval Gate (P0-7)
=============================================================================
创始人终审 Phase1 Step5 + ChatGPT P0-7 核心: 必须防止无限 Recovery Loop:

    Failure → Auto Fix → Wrong Fix → More Failure → Infinite Recovery

LAO 必须具备:
- RecoveryBudget: 累积 recovery 尝试预算(次数/时间/成本)·超→触发
- FailureEscalation: 超预算 → 逐级升级
- SafeMode: 超预算 → 进入安全模式(停止自动修复·改人工/降级)
- HumanApprovalGate: 关键/超预算恢复 → 需人类批准

信任原则:
- 禁止无限自我破坏(dangerous loop)
- Budget 超限 → SafeMode → HumanApprovalGate(人工接管)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional


STATE_ACTIVE = "active"
STATE_SAFE_MODE = "safe_mode"
STATE_ESCALATED = "escalated"
STATE_NEEDS_APPROVAL = "needs_approval"
STATE_ASCALATED_APPROVED = "escalated_approved"


@dataclass
class RecoveryBudget:
    """Recovery 预算(防无限自愈循环)。"""
    max_attempts: int = 3            # 最大自动恢复尝试次数
    max_window_seconds: int = 600    # 时间窗内 max_attempts(防风暴)
    cost_per_attempt: float = 0.0    # 每次恢复成本(可选)
    max_cost: float = 0.0            # 总成本上限(0=不限)

    attempts: List[float] = field(default_factory=list)   # 每次尝试时间戳
    total_cost: float = 0.0
    state: str = STATE_ACTIVE
    human_approved: bool = False
    pivot_reason: str = ""

    def record_attempt(self, now_ts: float | None = None) -> None:
        """记录一次恢复尝试·检查预算是否超限。"""
        import time
        now = now_ts if now_ts is not None else time.time()
        self.attempts.append(now)
        # 时间窗内去旧
        self.attempts = [t for t in self.attempts if now - t <= self.max_window_seconds]
        # 检查状态迁移
        if len(self.attempts) > self.max_attempts:
            self.state = STATE_SAFE_MODE
            self.pivot_reason = f"attempts={len(self.attempts)} > max={self.max_attempts}"
        elif self.max_cost and self.total_cost > self.max_cost:
            self.state = STATE_SAFE_MODE
            self.pivot_reason = f"cost={self.total_cost:.2f} > max={self.max_cost}"

    def add_cost(self, c: float) -> None:
        self.total_cost += c
        if self.max_cost and self.total_cost > self.max_cost:
            self.state = STATE_SAFE_MODE
            self.pivot_reason = f"cost={self.total_cost:.2f} > max={self.max_cost}"

    def needs_human_approval(self) -> bool:
        """是否需人工批准: SafeMode 且未批 → 需人工。"""
        return self.state == STATE_SAFE_MODE and not self.human_approved

    def approve(self) -> None:
        """人类批准继续(人工接管后)."""
        self.human_approved = True
        self.state = STATE_ESCALATED if not self.pivot_reason else STATE_ASCALATED_APPROVED


STATE_ASCALATED_APPROVED = "escalated_approved"


@dataclass
class RecoveryDecision:
    """Recovery 决策(带 budget 门控)。"""
    budget: RecoveryBudget
    action: str = "proceed"          # proceed / safe_mode / escalate / wait_approval
    reason: str = ""
    ts: str = ""

    def to_trust_event(self) -> dict:
        return {
            "event": "RecoveryDecision",
            "subtype": "RecoveryEvent",
            "domain": "runtime",
            "action": self.action,
            "reason": self.reason,
            "state": self.budget.state,
            "attempts": len(self.budget.attempts),
            "max_attempts": self.budget.max_attempts,
            "total_cost": round(self.budget.total_cost, 4),
            "human_approved": self.budget.human_approved,
            "ts": self.ts,
        }


class RecoveryGate:
    """Recovery 门控: 每次恢复前检查 budget → 决定 proceed/safe_mode/escalate/wait_approval。"""

    def __init__(self, budget: RecoveryBudget):
        self.budget = budget

    def check_before_recovery(self) -> RecoveryDecision:
        """恢复动作执行前调用: 门控是否放行。"""
        import time
        d = RecoveryDecision(budget=self.budget, ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        # 已触发 SafeMode 且未人工批准 → 等待批准
        if self.budget.state == STATE_SAFE_MODE:
            if not self.budget.human_approved:
                d.action = "wait_approval"
                d.reason = f"safe_mode: {self.budget.pivot_reason} → 需人工批准"
            else:
                d.action = "escalate"
                d.reason = "safe_mode: 人工已批准, 升级为人工指导恢复"
            return d
        # 预算仍有 → 放行
        d.action = "proceed"
        d.reason = "budget_ok: 允许本次恢复尝试"
        return d

    def after_attempt(self, success: bool, cost: float = 0.0) -> RecoveryDecision:
        """恢复动作执行后: 记录尝试+成本·更新状态。"""
        import time
        self.budget.add_cost(cost)
        self.budget.record_attempt()
        d = RecoveryDecision(budget=self.budget, ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        if not success and self.budget.state == STATE_SAFE_MODE:
            d.action = "safe_mode"
            d.reason = f"恢复失败+超预算 → SAFE MODE: {self.budget.pivot_reason}"
        elif not success:
            d.action = "proceed"
            d.reason = "恢复失败但预算未超·可重试"
        else:
            d.action = "resolved"
            d.reason = "恢复成功·预算已结"
        # 重置 attempts(成功后)
        if success:
            self.budget.attempts = []
        return d


# 便捷工厂: 标准 recovery 门控(Test 用模板)
def make_recovery_gate(max_attempts: int = 3) -> RecoveryGate:
    return RecoveryGate(RecoveryBudget(max_attempts=max_attempts))
