"""Phase 2 · Runtime State Registry 台账测试（Momo 负责模块）。

覆盖: 台账登记 / 状态更新 / health_score 计算 / 恢复与失败历史 /
      persistence 持久化 / summary 台账视角。
"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ris.state import RuntimeStateRegistry, RuntimeStateRecord, MAX_FAILURE_HISTORY
from lao.effect_anchored.runtime_registry import RuntimeRegistry, AgentRuntimeState

AGENTS = ["stella", "zeus", "shuyu", "tristan", "nova", "momo", "ethan", "baron", "luna"]


def _tmp_store():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    return path


# ── 登记 ──────────────────────────────────────────────────────────────
def test_register_nine_agents_idempotent():
    reg = RuntimeStateRegistry(store_path=_tmp_store())
    for a in AGENTS:
        reg.register(a, runtime="openclaw")
    assert len(reg.all()) == 9
    # 幂等
    reg.register("stella", runtime="hermes")
    assert len(reg.all()) == 9
    assert reg.get("stella").runtime == "hermes"


# ── 更新 / 状态 ───────────────────────────────────────────────────────
def test_update_from_runtime_syncs_record():
    """从 AgentRuntimeState 同步进台账。"""
    ledger = RuntimeStateRegistry(store_path=_tmp_store())
    runtime = RuntimeRegistry()
    runtime.register("stella", did="did:zwf:stella")
    runtime.set_status("stella", "online")
    rec = ledger.update_from_runtime(runtime.get("stella"))
    assert rec.status == "online"
    assert rec.health == "healthy"
    assert rec.health_score == 100.0


def test_recovering_state_scores_low():
    """recovering → health_score 低(40)+ unhealthy。"""
    ledger = RuntimeStateRegistry(store_path=_tmp_store())
    ledger.update("zeus", status="recovering", domain="gateway")
    rec = ledger.get("zeus")
    assert rec.status == "recovering"
    assert rec.health == "unhealthy"
    assert rec.health_score == 40.0
    assert rec.failure_history and rec.failure_history[-1].domain == "gateway"


# ── 恢复 / 失败历史 ──────────────────────────────────────────────────
def test_record_recovery_updates_last_recovery():
    """恢复→ online + last_recovery 记录。"""
    ledger = RuntimeStateRegistry(store_path=_tmp_store())
    ledger.update("nova", status="recovering", domain="provider")
    ledger.record_recovery("nova", method="L1_restart")
    rec = ledger.get("nova")
    assert rec.status == "online"
    assert rec.health == "healthy"
    assert rec.health_score == 100.0
    assert rec.last_recovery != ""
    assert rec.last_recovery_method == "L1_restart"


def test_failure_history_appends_and_lowers_score():
    """失败→ history 追加 + health_score 下降。"""
    ledger = RuntimeStateRegistry(store_path=_tmp_store())
    ledger.register("ethan")
    for _ in range(3):
        ledger.record_failure("ethan", domain="gateway")
    rec = ledger.get("ethan")
    assert len(rec.failure_history) == 3
    assert rec.health_score == 50.0 - 3 * 8.0  # 50 - 24 = 26
    assert rec.status == "recovering"  # < 40 → recovering


def test_failure_history_ring_capped():
    """失败历史环形上限 MAX_FAILURE_HISTORY。"""
    ledger = RuntimeStateRegistry(store_path=_tmp_store())
    ledger.register("baron")
    for _ in range(MAX_FAILURE_HISTORY + 10):
        ledger.record_failure("baron", domain="network")
    assert len(ledger.get("baron").failure_history) == MAX_FAILURE_HISTORY


# ── 台账汇总视角 ─────────────────────────────────────────────────────
def test_summary_who_healthy_who_recovering():
    """台账视角: 谁健康/谁异常/谁恢复中。"""
    ledger = RuntimeStateRegistry(store_path=_tmp_store())
    for a in AGENTS:
        ledger.register(a)
        ledger.update(a, status="online")
    ledger.update("zeus", status="recovering", domain="gateway")
    ledger.update("nova", status="degraded")
    s = ledger.summary()
    assert s["total"] == 9
    assert s["healthy"] == 7
    assert s["recovering"] == 1
    assert s["degraded"] == 1
    assert s["offline"] == 0
    assert s["avg_health_score"] > 0


# ── 持久化 ───────────────────────────────────────────────────────────
def test_persist_and_reload_roundtrip():
    """持久化 → 重启(新实例) → 台账可重建。"""
    path = _tmp_store()
    ledger = RuntimeStateRegistry(store_path=path)
    ledger.update("shuyu", status="online")
    ledger.record_failure("tristan", domain="provider")
    ledger.record_recovery("tristan", method="L2_hard")
    written = ledger.persist()
    assert written == 2

    # 模拟重启
    reloaded = RuntimeStateRegistry(store_path=path)
    assert len(reloaded.all()) == 2
    t = reloaded.get("tristan")
    assert t.status == "online"
    assert t.last_recovery_method == "L2_hard"
    assert len(t.failure_history) == 1
    assert t.failure_history[0].domain == "provider"
