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

    def test_level3_kill(self):
        """≥100% → 熔断(拒绝)"""
        g = CostGuard(budget_limit=100.0)
        g.record_spend(105.0)  # 105%
        r = g.check()
        assert r["level"] == 3
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
