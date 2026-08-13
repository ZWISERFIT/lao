"""Phase1 Step5 测试: Recovery Budget + Safe Mode + Human Approval Gate (P0-7)。

创始人终审 2026-08-13 P0-7: 防止无限 Recovery 循环。
Failure → AutoFix → WrongFix → MoreFailure → InfiniteLoop 必须被 Budget 拦住。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.recovery_budget import (
    RecoveryBudget, RecoveryGate, make_recovery_gate,
    STATE_SAFE_MODE, STATE_ACTIVE, STATE_ESCALATED,
)


def test_proceed_when_budget_ok():
    """预算未超 → 放行(proceed)。"""
    gate = make_recovery_gate(max_attempts=3)
    pre = gate.check_before_recovery()
    assert pre.action == "proceed"


def test_repeated_failure_enters_safe_mode():
    """连续失败超过预算 → 进入 SafeMode(防无限自愈)。"""
    gate = make_recovery_gate(max_attempts=3)
    # 反复失败恢复
    for _ in range(5):
        pre = gate.check_before_recovery()
        if pre.action == "proceed":
            gate.after_attempt(success=False)
    assert gate.budget.state == STATE_SAFE_MODE
    # 一旦 SafeMode → check 返回 wait_approval 而不是继续堆错误
    pre = gate.check_before_recovery()
    assert pre.action in ("wait_approval", "escalate")


def test_safe_mode_needs_human_approval():
    """SafeMode 未批准 → needs_approval=True; 批准后 → escalated。"""
    gate = make_recovery_gate(max_attempts=2)
    for _ in range(4):
        pre = gate.check_before_recovery()
        if pre.action == "proceed":
            gate.after_attempt(success=False)
    assert gate.budget.state == STATE_SAFE_MODE
    assert gate.budget.needs_human_approval() is True
    gate.budget.approve()
    assert gate.budget.human_approved is True
    assert gate.budget.state == STATE_ESCALATED


def test_success_resets_budget():
    """恢复成功 → attempts 清空, 状态恢复 active。"""
    gate = make_recovery_gate(max_attempts=3)
    gate.after_attempt(success=True)
    assert gate.budget.attempts == []
    assert gate.budget.state == STATE_ACTIVE


def test_trust_event_emitted():
    """RecoveryDecision 必须产出 TrustEvent(可审计)。"""
    gate = make_recovery_gate(max_attempts=3)
    pre = gate.check_before_recovery()
    te = pre.to_trust_event()
    assert te["subtype"] == "RecoveryEvent"
    assert te["event"] == "RecoveryDecision"
    assert "max_attempts" in te
