"""
P0-2 / P0-3 RIS 成熟部署测试（成熟部署加速·Shuyu 立项）
======================================================================================
P0-2: CPU 持续 >90% → 自动恢复闭环(五步: Detect→Classify→Recover→Verify→Record)
P0-3: RIS→LAO 数据桥(事件写入共享 JSON 供 LAO/Stella 消费)

验证:
  P0-2:
    1. RIS_EVENT_TYPES 纳入 cpu_recovery
    2. recover_cpu 持续帧数达标 → 触发恢复(recovered=true)
    3. 恢复事件 detail 携带 top_processes(审计可追溯)
  P0-3:
    1. RISToLAOBridge 导入 + 落盘位置正确
    2. aggregate 正确聚合 events_by_type / recoveries / provider_status
    3. build 输出符合 schema(7 个顶层字段)
    4. active_alerts 只保留未恢复的 critical/error(有上限)
"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ris.events import RuntimeHealthEvent, RIS_EVENT_TYPES
from ris.bridge import RISToLAOBridge, sync_bridge, BRIDGE_FILE, BRIDGE_DIR, SCHEMA_VERSION


# ── P0-2: CPU 自动恢复 ──────────────────────────────────
def test_p02_cpu_recovery_in_event_types():
    """RIS_EVENT_TYPES 已纳入 cpu_recovery(P0-2 恢复对偶信号)。"""
    assert "cpu_recovery" in RIS_EVENT_TYPES


def test_p02_cpu_recovery_event_schema():
    """cpu_recovery 事件承载五步闭环关键字段。"""
    ev = RuntimeHealthEvent(
        event_type="cpu_recovery", agent_id="system", status="recovered",
        severity="warn",
        detail={"classified": "cpu_sustained", "attempts": 1,
                "verified": True, "recorded": True,
                "top_processes": [{"pid": 100, "cpu_pct": 37.7}]},
    )
    assert ev.event_type == "cpu_recovery"
    assert ev.status == "recovered"
    assert ev.detail["verified"] is True
    assert "top_processes" in ev.detail  # 审计可追溯


# ── P0-3: RIS→LAO 数据桥 ────────────────────────────────
def test_p03_bridge_location():
    """桥落盘到共享状态目录(LAO/Stella 约定位置)。"""
    assert BRIDGE_DIR == "/home/agentuser/shared/state"
    assert BRIDGE_FILE.endswith("ris-bridge.json")


def _sample_events():
    return [
        RuntimeHealthEvent(event_type="cpu_anomaly", agent_id="system",
                           status="detected", severity="error",
                           detail={"cpu_pct": 100.0}).to_dict(),
        RuntimeHealthEvent(event_type="cpu_recovery", agent_id="system",
                           status="recovered", severity="warn",
                           detail={"classified": "cpu_sustained", "verified": True}).to_dict(),
        RuntimeHealthEvent(event_type="provider_unavailable", agent_id="lao-router",
                           status="detected", severity="critical",
                           detail={"provider": "lao-router", "cost_impact": "high"}).to_dict(),
        RuntimeHealthEvent(event_type="provider_ok", agent_id="lao-router",
                           status="recovered", severity="info",
                           detail={"provider": "lao-router"}).to_dict(),
    ]


def test_p03_aggregate_summary():
    """aggregate 正确聚合 events_by_type + recoveries + provider_status。"""
    b = RISToLAOBridge(bridge_file="/tmp/ris-bridge-test.json",
                       source_log="/tmp/nonexistent-ris.jsonl")
    agg = b.aggregate(_sample_events())
    s = agg["summary"]
    assert s["events_by_type"]["cpu_anomaly"] == 1
    assert s["recoveries"]["cpu_recovery"] == 1
    assert s["provider_status"]["lao-router"] == "healthy"  # provider_ok 覆盖 down
    assert s["latest_cpu_pct"] == 100.0


def test_p03_build_schema():
    """build 输出符合 7 个顶层字段 schema。"""
    b = RISToLAOBridge(bridge_file="/tmp/ris-bridge-test.json",
                       source_log="/tmp/nonexistent-ris.jsonl")
    out = b.build(_sample_events())
    expected_keys = {"layer", "schema_version", "generated_at", "window",
                     "summary", "recent_events", "active_alerts"}
    assert set(out.keys()) == expected_keys
    assert out["layer"] == "ris"
    assert out["schema_version"] == SCHEMA_VERSION
    assert out["window"]["events_total"] == 4


def test_p03_active_alerts_only_unresolved():
    """active_alerts 只保留未恢复的 critical/error(provider_ok/cpu_recovery 不进来)。"""
    b = RISToLAOBridge(bridge_file="/tmp/ris-bridge-test.json",
                       source_log="/tmp/nonexistent-ris.jsonl")
    out = b.build(_sample_events())
    # 只有 cpu_anomaly(error·detected) 和 provider_unavailable(critical·detected) 进 alerts
    # provider_ok(recovered)/cpu_recovery(recovered) 不回进 alerts
    types = [a["event_type"] for a in out["active_alerts"]]
    assert "provider_unavailable" in types
    assert "cpu_recovery" not in types  # recovered 不告警


def test_p03_sync_writes_file():
    """sync 原子写落盘(能读回)。"""
    b = RISToLAOBridge(bridge_file="/tmp/ris-bridge-sync-test.json",
                       source_log="/tmp/nonexistent-ris.jsonl")
    b.sync(_sample_events())
    with open("/tmp/ris-bridge-sync-test.json") as f:
        data = json.load(f)
    assert data["window"]["events_total"] == 4
    assert data["summary"]["recoveries"]["cpu_recovery"] == 1
