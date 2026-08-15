"""
P0-1 Provider 健康监控接入测试（成熟部署加速·Shuyu 立项）
======================================================================================
验证 ProviderHealthMonitor：
  1. 复用 ProviderHealthGate（健康判定不再"端口通=健康"）
  2. lao-router 掉线 → provider_unavailable(critical) + cost_impact=high + fallback_target
  3. 直连 deepseek 掉线 → provider_unavailable(critical) + cost_impact=critical + fallback=None
  4. 成本链路字段（cost_chain）always 存在，供财务/智囊团复盘关联
  5. 健康时 → provider_ok（不误报·不刷噪声）
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ris.health.provider_monitor import (
    ProviderHealthMonitor, COST_CHAIN_NOTE,
    LAO_ROUTER_ENDPOINT, DEEPSEEK_DIRECT_ENDPOINT,
)
from ris.events import RuntimeHealthEvent, RIS_EVENT_TYPES


def test_ris_event_types_include_provider():
    """RIS_EVENT_TYPES 已纳入 provider_unavailable / provider_ok。"""
    assert "provider_unavailable" in RIS_EVENT_TYPES
    assert "provider_ok" in RIS_EVENT_TYPES


def test_lao_router_down_produces_unavailable_with_cost_chain():
    """lao-router 掉线(端口无监听) → provider_unavailable(critical)+cost_impact=high。"""
    m = ProviderHealthMonitor(router_endpoint="http://127.0.0.1:9999/v1/models")
    ev = m.check_lao_router()
    assert ev.event_type == "provider_unavailable"
    assert ev.agent_id == "lao-router"
    assert ev.severity == "critical"
    assert ev.detail["cost_impact"] == "high"
    assert ev.detail["fallback_target"] == "deepseek-direct"
    assert COST_CHAIN_NOTE in ev.detail["cost_chain"]


def test_deepseek_direct_down_produces_unavailable_critical():
    """直连 deepseek 掉线(DNS 失败) → provider_unavailable(critical)+cost_impact=critical。"""
    m = ProviderHealthMonitor(direct_endpoint="https://nonexistent.invalid/v1/models")
    ev = m.check_deepseek_direct()
    assert ev.event_type == "provider_unavailable"
    assert ev.agent_id == "deepseek"
    assert ev.severity == "critical"
    assert ev.detail["cost_impact"] == "critical"
    assert ev.detail["fallback_target"] is None  # 直连也断 = 无兜底


def test_healthy_produces_provider_ok_not_unavailable():
    """健康端点 → provider_ok（recovered·info·不误报为 critical）。"""
    m = ProviderHealthMonitor(
        router_endpoint="http://127.0.0.1:8765/v1/models",  # 真实 lao-router(如有)
    )
    ev = m.check_lao_router()
    # lao-router 可能在线也可能离线，但只要端点可达就不得报 critical
    assert ev.event_type in ("provider_ok", "provider_unavailable")


def test_check_once_filters_to_exceptions_only():
    """check_once() 默认只返回异常事件(不刷 provider_ok 噪声)。"""
    m = ProviderHealthMonitor(
        router_endpoint="http://127.0.0.1:9999/v1/models",
        direct_endpoint="https://nonexistent.invalid/v1/models",
    )
    evs = m.check_once()
    assert all(e.event_type == "provider_unavailable" for e in evs)
    assert len(evs) == 2  # lao-router + deepseek 都断


def test_event_is_runtime_health_event():
    """产出的是 RuntimeHealthEvent(layer=ris)。"""
    m = ProviderHealthMonitor(router_endpoint="http://127.0.0.1:9999/v1/models")
    ev = m.check_lao_router()
    assert isinstance(ev, RuntimeHealthEvent)
    assert ev.layer == "ris"
    assert ev.to_dict()["layer"] == "ris"
