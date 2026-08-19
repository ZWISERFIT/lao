"""P1-1 RIS 成本护栏三级化测试（2026-08-19 Shuyu审定·外部案例D·Bifrost）。

验证: Alert→Throttle→Kill 三级渐进响应(70/90/100%)。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ris.cost_guard import CostGuard


class TestLevelProgression:
    """三级渐进触发"""

    def test_level0_normal(self):
        """低于70% → 正常放行"""
        g = CostGuard(budget_limit=100.0)
        r = g.check()
        assert r["level"] == 0
        assert r["action"] == "normal"
        assert r["allowed"] is True

    def test_level1_alert(self):
        """70-90% → 告警(仍放行)"""
        g = CostGuard(budget_limit=100.0)
        g.record_spend(75.0)  # 75%
        r = g.check()
        assert r["level"] == 1
        assert r["action"] == "alert"
        assert r["allowed"] is True

    def test_level2_throttle(self):
        """90-100% → 节流(放行但标记降级)"""
        g = CostGuard(budget_limit=100.0)
        g.record_spend(95.0)  # 95%
        r = g.check()
        assert r["level"] == 2
        assert r["action"] == "throttle"
        assert r["allowed"] is True

    def test_level3_kill_limited_and_diagnosed(self):
        """护栏1: Kill 仅限指定模块 + 前置诊断确认死循环"""
        # 非 kill_modules → 降级 Throttle(不拒正常流量)
        g0 = CostGuard(budget_limit=100.0, name="normal-module")
        g0.record_spend(105.0)
        assert g0.check()["allowed"] is True
        assert g0.check()["action"] == "throttle"

        # kill_modules 但无诊断 → 降级 Throttle(防误 Kill 正常峰值)
        g1 = CostGuard(budget_limit=100.0, name="loop-module",
                       kill_modules=frozenset({"loop-module"}))
        g1.record_spend(105.0)
        assert g1.check()["action"] == "throttle"

        # kill_modules + 诊断确认死循环 → Kill
        g2 = CostGuard(budget_limit=100.0, name="loop-module",
                       kill_modules=frozenset({"loop-module"}))
        g2.record_spend(105.0)
        r = g2.check(diagnose={"root_cause": "dead_loop"})
        assert r["action"] == "kill"
        assert r["allowed"] is False

    def test_boundary_values(self):
        """边界值: 恰好70%→alert·恰好90%→throttle·恰好100%→kill"""
        g1 = CostGuard(budget_limit=100.0)
        g1.record_spend(70.0)
        assert g1.level() == 1

        g2 = CostGuard(budget_limit=100.0)
        g2.record_spend(90.0)
        assert g2.level() == 2

        g3 = CostGuard(budget_limit=100.0)
        g3.record_spend(100.0)
        assert g3.level() == 3


class TestRecordAndReset:
    def test_record_spend_updates_level(self):
        """记账后级别更新"""
        g = CostGuard(budget_limit=100.0)
        r = g.record_spend(95.0)
        assert r["level"] == 2
        assert r["spent"] == 95.0

    def test_reset(self):
        """重置后回 level 0"""
        g = CostGuard(budget_limit=100.0)
        g.record_spend(95.0)
        g.reset()
        assert g.level() == 0


class TestPerModule:
    def test_per_module_isolation(self):
        """per-module 独立预算(P1-3 支持)"""
        g_a = CostGuard(budget_limit=100.0, name="module-a")
        g_b = CostGuard(budget_limit=100.0, name="module-b")
        g_a.record_spend(95.0)
        assert g_a.level() == 2
        assert g_b.level() == 0  # module-b 不受影响


class TestGuardrail2:
    """护栏2: Throttle 只动降级层·heavy/reasoning/code 宁贵勿错"""

    def test_non_degradable_no_throttle(self):
        g = CostGuard(budget_limit=100.0, name="reasoning", degradable=False)
        g.record_spend(95.0)  # 95% → 本应 Throttle
        r = g.check()
        assert r["action"] != "throttle"  # 不降级
        assert r["allowed"] is True  # 宁贵勿错·仍放行

    def test_degradable_throttles(self):
        g = CostGuard(budget_limit=100.0, name="ul_light", degradable=True)
        g.record_spend(95.0)
        r = g.check()
        assert r["action"] == "throttle"
