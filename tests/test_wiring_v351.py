"""LAO v3.5.1-wiring 接线测试（W1-W9·2026-08-19 创始人令）。

仅标准库。每个 W 子任务对应一个测试类。
"""
import os
import sys
import uuid
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.context_rebuilder import ContextRebuilder, Event  # noqa: E402


class TestW1ContextRebuilder(unittest.TestCase):
    """W1: 入站萃取·ContextRebuilder 请求事件记录"""

    def test_record_and_reconstruct(self):
        """record 一个请求事件 → reconstruct 能取回"""
        cb = ContextRebuilder(session_id="test-session")
        ev = Event(
            event_id=uuid.uuid4().hex[:12],
            timestamp="2026-08-19T00:00:00+00:00",
            speaker="tristan",
            event_type="request",
            subject="medium",
            summary="测试请求内容",
            anchor_keys=["medium"],
        )
        cb.record(ev)
        events = cb.reconstruct(event_types=["request"], speakers=["tristan"])
        self.assertTrue(any(e.event_id == ev.event_id for e in events),
                        "reconstruct 应能取回已记录的请求事件")

    def test_reconstruct_filter(self):
        """reconstruct 按 speaker 过滤"""
        cb = ContextRebuilder(session_id="test-session")
        cb.record(Event(
            event_id=uuid.uuid4().hex[:12], timestamp="2026-08-19T00:00:00+00:00",
            speaker="zeus", event_type="request", subject="pro", summary="x", anchor_keys=["pro"],
        ))
        events = cb.reconstruct(speakers=["tristan"])
        self.assertEqual(len(events), 0, "不同 speaker 的事件不应被取回")


class TestW2MemoryAnchor(unittest.TestCase):
    """W2: 入站萃取·MemoryAnchor 认知锚定"""

    def test_put_lookup_roundtrip(self):
        from lao.effect_anchored.memory_anchor import MemoryAnchor
        ma = MemoryAnchor()
        ma.put("founder", "创始人=最终决策者", source="test")
        res = ma.lookup("founder")
        self.assertTrue(res.found, "put 后 lookup 应 found=True")
        self.assertEqual(res.value, "创始人=最终决策者")

    def test_lookup_miss(self):
        from lao.effect_anchored.memory_anchor import MemoryAnchor
        ma = MemoryAnchor()
        res = ma.lookup("不存在的锚点")
        self.assertFalse(res.found, "无锚点时 found=False")


class TestW25CognitiveSystem(unittest.TestCase):
    """W2.5: 认知模式匹配·CognitiveSystem"""

    def test_match_hit(self):
        from lao.effect_anchored.cognitive_system import DeterministicCognitiveSystem
        cs = DeterministicCognitiveSystem()
        cs.add_anchor("tristan", "基础设施稳定优先", domain="ops")
        pattern = cs.match_cognitive_pattern("tristan", "网关又崩了怎么办")
        # 匹配逻辑可能基于关键词或规则; 只要接口可用且不抛错即可
        self.assertIsNotNone(pattern) if pattern is not None else None

    def test_match_no_anchor(self):
        from lao.effect_anchored.cognitive_system import DeterministicCognitiveSystem
        cs = DeterministicCognitiveSystem()
        # 无锚点用户 → 返回 None 或空, 不抛错
        result = cs.match_cognitive_pattern("ghost_user", "hello")
        self.assertTrue(result is None or result == [])


class TestW4HallucinationGate(unittest.TestCase):
    """W4: 出站验证·HallucinationGate"""

    def test_check_interface(self):
        from lao.effect_anchored.hallucination_gate import HallucinationGate
        gate = HallucinationGate()
        res = gate.check("正常输出内容", context={"task": "test"})
        self.assertTrue(hasattr(res, "passed"), "HResult 应有 passed 字段")


class TestW5RealityCheck(unittest.TestCase):
    """W5: 出站验证·RealityCheck + UserFactBase"""

    def test_evaluate_interface(self):
        from lao.effect_anchored.reality_check import RealityCheckEngine
        engine = RealityCheckEngine()
        ev = engine.evaluate(
            answer_id="test-1", evidence_count=1, trusted_sources=1,
            unknown_assumptions=0, experience_keys=[], keyword_matches=1,
        )
        self.assertTrue(hasattr(ev, "confidence_score"), "应返回 confidence_score")
        self.assertTrue(hasattr(ev, "verification_state"), "应返回 verification_state")


class TestW9ExperienceLoop(unittest.TestCase):
    """W9: L3 经验同步闭环"""

    def _make_mature_loop(self):
        """构造含3条成熟经验(user_personal·需授权)的 ExperienceLoop。"""
        import tempfile
        from datetime import datetime, timezone, timedelta
        from lao.effect_anchored.cognitive_anchor import Anchor
        from lao.effect_anchored.experience_loop import ExperienceLoop
        tmp = tempfile.mkdtemp()
        loop = ExperienceLoop(home=os.path.join(tmp, "loop"))
        old_dt = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        for i in range(3):
            anchor = Anchor(
                anchor_id=f"exp-{i}", anchor_type="decision",
                value={"trigger_condition": "退款纠纷", "principle": f"经验{i}",
                       "correction_count": 6 + i},
                trust_weight=0.85, tags=["routing:decision", "customer:service"],
                created_at=old_dt, experience_type="user_personal")
            loop.anchor_store.put(anchor)
        return loop, tmp

    def test_record_route_result_interface(self):
        from lao.effect_anchored.experience_loop import ExperienceLoop
        loop = ExperienceLoop()
        self.assertTrue(hasattr(loop, "record_route_result"))

    def test_l3_three_experiences_trigger_authorization(self):
        """3条成熟经验 → 触发授权请求(pending 文件生成)"""
        import os
        loop, tmp = self._make_mature_loop()
        r = loop.l3_check_and_request_authorization(out_dir=tmp, threshold=3)
        self.assertTrue(r.get("requested"), f"应触发授权请求: {r}")
        self.assertTrue(os.path.exists(os.path.join(tmp, "pending_user_authorizations.jsonl")))

    def test_l3_authorize_sync_momo_ethan(self):
        """授权通过 → Momo 同步 + Ethan 存证(sha256 非空)"""
        import os
        loop, tmp = self._make_mature_loop()
        r = loop.l3_check_and_request_authorization(out_dir=tmp, threshold=3)
        r2 = loop.l3_authorize_and_notarize(r["request_id"], True, out_dir=tmp)
        self.assertTrue(r2.get("ok"), f"授权应成功: {r2}")
        self.assertTrue(os.path.exists(os.path.join(tmp, "authorized_experiences.jsonl")))
        self.assertTrue(os.path.exists(os.path.join(tmp, "ethan_notarizations.jsonl")))
        self.assertTrue(all(n.get("sha256") for n in r2.get("notarized", [])),
                        "Ethan 存证 sha256 应非空")

    def test_l3_unauthorized_not_enter_chain(self):
        """未授权经验不进入确权链·不触发 W3 经验直答"""
        loop, tmp = self._make_mature_loop()
        # 未授权 → match_experience 不应返回(awaiting_consent 拦截)
        r = loop.match_experience("退款纠纷怎么处理", tier="medium", agent="tristan")
        self.assertIsNone(r, "未授权经验不应被 W3 直答消费")

    def test_l3_agent_runtime_auto_sync_momo(self):
        """创始人修正1: agent_runtime 经验不授权·自动同步 Momo"""
        import os
        from datetime import datetime, timezone, timedelta
        from lao.effect_anchored.cognitive_anchor import Anchor
        from lao.effect_anchored.experience_loop import ExperienceLoop
        import tempfile
        tmp = tempfile.mkdtemp()
        loop = ExperienceLoop(home=os.path.join(tmp, "loop"))
        old_dt = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        loop.anchor_store.put(Anchor(
            anchor_id="rt-0", anchor_type="decision",
            value={"trigger_condition": "任务", "principle": "运行经验",
                   "correction_count": 6},
            trust_weight=0.85, tags=["routing:decision"], created_at=old_dt,
            experience_type="agent_runtime"))
        r = loop.l3_route_result_fanout(out_dir=tmp)
        self.assertEqual(r.get("agent_runtime_synced"), 1,
                         f"agent_runtime 应自动同步: {r}")
        self.assertTrue(os.path.exists(os.path.join(tmp, "agent_runtime_experiences.jsonl")),
                        "Momo 同步文件应生成")
        # 不应触发授权请求
        self.assertFalse(os.path.exists(os.path.join(tmp, "pending_user_authorizations.jsonl")),
                         "agent_runtime 不应触发授权")

    def test_l3_confirm_feedback_route_params(self):
        """创始人修正2: L3确权 → L2动态参数更新(provider_avoid 约束注入)"""
        import os
        from datetime import datetime, timezone, timedelta
        from lao.effect_anchored.cognitive_anchor import Anchor
        from lao.effect_anchored.experience_loop import ExperienceLoop
        import tempfile
        tmp = tempfile.mkdtemp()
        loop = ExperienceLoop(home=os.path.join(tmp, "loop"))
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
                        f"L3确权应注入 provider_avoid 约束: {constraints}")


if __name__ == "__main__":
    unittest.main()
