"""Phase2 P0-1 测试: Agent Runtime Registry (外部开发者第一印象)。

创始人 v3.4 令: 用户必须实时知道 Agent 是否活着。
外部体验: Stella ✓ online / Zeus ⚠ recovering / Nova ▲ degraded。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.runtime_registry import RuntimeRegistry

AGENTS = ["stella", "zeus", "shuyu", "tristan", "nova", "momo", "ethan", "baron", "luna"]


def test_register_nine_agents():
    """9 Agent 注册(idempotent·可重复)。"""
    reg = RuntimeRegistry()
    for a in AGENTS:
        reg.register(a, did=f"did:zwf:{a}")
    assert reg.summary()["total"] == 9
    # 重复注册不重复
    reg.register("stella", did="did:zwf:stella")
    assert reg.summary()["total"] == 9


def test_online_status_healthy():
    """online → healthy。"""
    reg = RuntimeRegistry()
    reg.register("stella")
    reg.set_status("stella", "online")
    st = reg.get("stella")
    assert st.status == "online"
    assert st.health == "healthy"


def test_recovering_with_failure_domain():
    """recovering → unhealthy + failure_domain + recovery_state。"""
    reg = RuntimeRegistry()
    reg.register("zeus")
    reg.set_status("zeus", "recovering", domain="gateway", recovery_state="attempt 1/3")
    st = reg.get("zeus")
    assert st.status == "recovering"
    assert st.health == "unhealthy"
    assert st.failure_domain == "gateway"
    assert st.recovery_state == "attempt 1/3"


def test_trust_score_tracks_success_failure():
    """成功→trust上升·失败→trust下降。"""
    reg = RuntimeRegistry()
    reg.register("nova")
    for _ in range(3):
        reg.record_success("nova")
    assert reg.get("nova").trust_score == 3.0
    reg.record_failure("nova", domain="provider")
    assert reg.get("nova").trust_score == 0.0  # 3 - 3 = 0


def test_summary_counts():
    """统计: online/recovering/degraded/offline 数。"""
    reg = RuntimeRegistry()
    for a in AGENTS:
        reg.register(a)
        reg.set_status(a, "online")
    reg.set_status("zeus", "recovering")
    reg.set_status("nova", "degraded")
    s = reg.summary()
    assert s["total"] == 9
    assert s["online"] == 7
    assert s["recovering"] == 1
    assert s["degraded"] == 1
    assert s["offline"] == 0
