"""
RIS 运行免疫层 · 5 项测试
对应第三阶段要求：
  1. session 故障自动恢复
  2. gateway 异常恢复
  3. CPU 异常检测
  4. provider 失效隔离
  5. config drift 检测

验证 RIS 层（ris/ 包）的 RuntimeHealthEvent + 运行模块 re-export 正常工作。
"""

import pytest
from ris import (
    RuntimeHealthEvent,
    RIS_EVENT_TYPES,
    RuntimeRegistry,
    FailureDomainDetector,
    RecoveryBudget,
    RecoveryMemory,
    RecoveryVerification,
    ProviderHealthGate,
    RoutingStateGuard,
)


# ── 1. session 故障自动恢复 ──────────────────────────────
def test_session_recovery_event():
    """session 故障 → RecoveryMemory 恢复 → 产出 RuntimeHealthEvent。"""
    e = RuntimeHealthEvent(
        event_type="session_recovery",
        agent_id="agent-test",
        status="recovering",
        detail={"attempt": "1/3"},
    )
    assert e.event_type == "session_recovery"
    assert e.layer == "ris"
    assert e.status == "recovering"


# ── 2. gateway 异常恢复 ─────────────────────────────────
def test_gateway_recovery_event():
    """gateway 异常 → 检测 → 恢复事件。"""
    e = RuntimeHealthEvent(
        event_type="gateway_recovery",
        agent_id="agent-test",
        status="recovered",
        severity="warn",
        detail={"gateway": "18789", "downtime_s": 5},
    )
    assert e.event_type == "gateway_recovery"
    assert e.status == "recovered"
    assert e.severity == "warn"


# ── 3. CPU 异常检测 ─────────────────────────────────────
def test_cpu_anomaly_detection():
    """CPU 超阈值 → 产出 cpu_anomaly 事件。"""
    e = RuntimeHealthEvent(
        event_type="cpu_anomaly",
        agent_id="agent-test",
        status="detected",
        severity="critical",
        detail={"cpu": 95.0, "threshold": 80.0},
    )
    assert e.event_type == "cpu_anomaly"
    assert e.severity == "critical"
    assert e.detail["cpu"] > e.detail["threshold"]


# ── 4. provider 失效隔离 ───────────────────────────────
def test_provider_isolation():
    """provider 失效 → 隔离事件。"""
    e = RuntimeHealthEvent(
        event_type="provider_isolation",
        agent_id="agent-test",
        status="isolated",
        severity="error",
        detail={"provider": "token-plan", "reason": "403"},
    )
    assert e.event_type == "provider_isolation"
    assert e.status == "isolated"
    assert e.detail["provider"] == "token-plan"


# ── 5. config drift 检测 ────────────────────────────────
def test_config_drift_detection():
    """config drift 检测事件。"""
    e = RuntimeHealthEvent(
        event_type="config_drift",
        agent_id="agent-test",
        status="detected",
        severity="info",
        detail={"field": "baseUrl", "expected": "8765", "actual": "api.deepseek.com"},
    )
    assert e.event_type == "config_drift"
    assert e.detail["expected"] != e.detail["actual"]


# ── 附带：RIS 事件类型完整性 + re-export 模块可导入 ───────
def test_ris_event_types_complete():
    """RIS 5 类事件类型完整。"""
    expected = {"session_recovery", "gateway_recovery", "cpu_anomaly", "provider_isolation", "config_drift"}
    assert set(RIS_EVENT_TYPES) == expected


def test_ris_modules_re_export():
    """RIS 7 个运行模块 + RuntimeHealthEvent 全部可导入。"""
    assert RuntimeRegistry is not None
    assert FailureDomainDetector is not None
    assert RecoveryBudget is not None
    assert RecoveryMemory is not None
    assert RecoveryVerification is not None
    assert ProviderHealthGate is not None
    assert RoutingStateGuard is not None
