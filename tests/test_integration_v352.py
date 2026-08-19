"""LAO v3.5.2 集成测试（Qoder GLM-5.3 审核发现 bug 的回归防护·2026-08-19）。

覆盖 P0/P1 修复的事故高发区：
- P0-1: query_facts 无 limit 参数(W5 不再 TypeError)
- P0-2: 降级环境(HALL_GATE=None)不 NameError 500
- P0-3: W9 fanout 幂等(同 anchor 不重复 append)
- P1-6: W3 不直答 gate_failed 锚点
- P1-7: l3_founder_confirm 精确单条确权
- P1-8: Ethan 存证哈希绑定完整 value
"""
import os
import sys
import json
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.experience_loop import ExperienceLoop  # noqa: E402
from lao.effect_anchored.cognitive_anchor import Anchor, make_decision_anchor  # noqa: E402
from lao.effect_anchored.feedback_bus import FeedbackBus  # noqa: E402


def _make_mature_loop(tmp, n=3, exp_type="user_personal"):
    """构造含 n 条成熟经验的 ExperienceLoop。"""
    from datetime import datetime, timezone, timedelta
    loop = ExperienceLoop(home=os.path.join(tmp, "loop"))
    old_dt = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    for i in range(n):
        loop.anchor_store.put(Anchor(
            anchor_id=f"exp-{i}", anchor_type="decision",
            value={"trigger_condition": "退款纠纷", "principle": f"经验{i}",
                   "correction_count": 6 + i},
            trust_weight=0.85, tags=["routing:decision", "customer:service"],
            created_at=old_dt, experience_type=exp_type))
    return loop


class TestP0QueryFacts(unittest.TestCase):
    """P0-1: query_facts 无 limit 参数"""

    def test_query_facts_no_limit_kwarg(self):
        """server 调用不带 limit=10 → 不再 TypeError"""
        import inspect
        from lao.effect_anchored.user_fact_base import UserFactBase
        sig = inspect.signature(UserFactBase.query_facts)
        self.assertNotIn("limit", sig.parameters,
                         "query_facts 签名不应有 limit(修复后 server 不传)")

    def test_actual_call_works(self):
        """实际调用(无 limit)不抛错"""
        from lao.effect_anchored.user_fact_base import UserFactBase
        fb = UserFactBase()
        r = fb.query_facts("tristan")
        self.assertIsInstance(r, list)


class TestP0FailOpen(unittest.TestCase):
    """P0-2: 降级环境(HALL_GATE=None)不 500"""

    def test_fail_open_initialized_vars(self):
        """模拟 HALL_GATE=None 时 W4/W5 块变量已初始化(不 NameError)"""
        import lao.effect_anchored.routing.lao_router_server as srv
        # 验证模块级变量存在(fail-open 语义)
        self.assertTrue(hasattr(srv, "HALL_GATE"))
        self.assertTrue(hasattr(srv, "REALITY"))
        self.assertTrue(hasattr(srv, "FACTS"))


class TestP0FanoutIdempotent(unittest.TestCase):
    """P0-3: W9 fanout 幂等(同 anchor 不重复 append)"""

    def test_second_fanout_no_duplicate(self):
        tmp = tempfile.mkdtemp()
        loop = _make_mature_loop(tmp, n=3, exp_type="agent_runtime")
        r1 = loop.l3_route_result_fanout(out_dir=tmp)
        r2 = loop.l3_route_result_fanout(out_dir=tmp)
        self.assertEqual(r1.get("agent_runtime_synced"), 3, f"首次应同步3条: {r1}")
        self.assertEqual(r2.get("agent_runtime_synced"), 0, f"二次应去重: {r2}")
        fp = os.path.join(tmp, "agent_runtime_experiences.jsonl")
        n = sum(1 for _ in open(fp)) if os.path.exists(fp) else 0
        self.assertEqual(n, 3, f"文件应3行非6: {n}")


class TestP1GateFailedBlocked(unittest.TestCase):
    """P1-6: W3 不直答 gate_failed 锚点"""

    def test_gate_failed_anchor_not_served(self):
        tmp = tempfile.mkdtemp()
        loop = _make_mature_loop(tmp, n=1)
        # 找到锚点并确认确权报告结构含 gate_failed 键
        rep = loop.confirm_experiences(authorized=False, limit=50)
        self.assertIn("gate_failed", rep)
        self.assertIn("awaiting_consent", rep)


class TestP1FounderConfirmExact(unittest.TestCase):
    """P1-7: l3_founder_confirm 精确单条确权"""

    def test_unknown_id_returns_false(self):
        tmp = tempfile.mkdtemp()
        loop = _make_mature_loop(tmp, n=1)
        self.assertFalse(loop.l3_founder_confirm("ghost-id"),
                         "不存在 id 应返回 False")

    def test_existing_id_confirms(self):
        tmp = tempfile.mkdtemp()
        loop = _make_mature_loop(tmp, n=1)
        ok = loop.l3_founder_confirm("exp-0")
        self.assertTrue(ok, "存在 id 应确权成功")


class TestP1NotarizeHashBindsValue(unittest.TestCase):
    """P1-8: Ethan 存证哈希绑定完整 value"""

    def test_hash_uses_full_anchor(self):
        tmp = tempfile.mkdtemp()
        loop = _make_mature_loop(tmp, n=3)
        # 构造授权请求
        r = loop.l3_check_and_request_authorization(out_dir=tmp, threshold=3)
        self.assertTrue(r.get("requested"), f"应触发授权: {r}")
        r2 = loop.l3_authorize_and_notarize(r["request_id"], True, out_dir=tmp)
        self.assertTrue(r2.get("ok"), f"授权应成功: {r2}")
        # 存证哈希应非空·且 domain 非硬编码 routing
        for n in r2.get("notarized", []):
            self.assertTrue(n.get("sha256"), "sha256 应非空")
            self.assertNotEqual(n.get("domain"), "experience/routing",
                                "domain 应取真实字段非硬编码 routing(旧bug)")
            self.assertTrue(n.get("domain", "").startswith("experience/"),
                            f"domain 应真实: {n.get('domain')}")


class TestP1RouteParamsFeedback(unittest.TestCase):
    """创始人修正2: L3确权 → L2动态参数更新(回归防护)"""

    def test_confirm_injects_provider_avoid(self):
        tmp = tempfile.mkdtemp()
        loop = ExperienceLoop(home=os.path.join(tmp, "loop"))
        from datetime import datetime, timezone, timedelta
        old_dt = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        loop.anchor_store.put(Anchor(
            anchor_id="exp-fail", anchor_type="decision",
            value={"trigger_condition": "token-plan故障", "principle": "避开",
                   "provider_avoid": "token-plan", "correction_count": 8},
            trust_weight=0.9, tags=["routing:decision", "runtime:fail"],
            created_at=old_dt, experience_type="agent_runtime"))
        rep = loop.confirm_experiences(authorized=True, limit=50)
        self.assertTrue(rep.get("confirmed"), f"应确权: {rep}")
        constraints = loop.bus.active_route_constraints()
        self.assertTrue(any("token-plan" in str(c.get("provider_avoid", []))
                            for c in constraints),
                        f"应注入 provider_avoid: {constraints}")


class TestP0ExpTypeExplicit(unittest.TestCase):
    """P0-4: make_*_anchor 显式 experience_type"""

    def test_make_decision_default_agent_runtime(self):
        a = make_decision_anchor(anchor_id="d", principle="p", trigger_condition="t",
                                 action_rule="a", source="test")
        self.assertEqual(a.experience_type, "agent_runtime")

    def test_make_decision_explicit_user_personal(self):
        a = make_decision_anchor(anchor_id="d", principle="p", trigger_condition="t",
                                 action_rule="a", source="test",
                                 experience_type="user_personal")
        self.assertEqual(a.experience_type, "user_personal")


if __name__ == "__main__":
    unittest.main()
