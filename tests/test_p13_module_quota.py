"""P1-3 LAO per-module 成本配额测试（2026-08-19 Shuyu审定·外部案例A[E]·$47k教训设计参考）。

验证: per-module 预算上限·防单模块跑飞·全局兜底。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.module_quota import ModuleQuota


class TestPerModuleQuota:
    def test_unregistered_module_allowed(self):
        """未注册模块 → 放行(向后兼容)"""
        q = ModuleQuota()
        r = q.check("unknown-module")
        assert r["allowed"] is True

    def test_within_limit(self):
        """模块配额内 → 放行"""
        q = ModuleQuota()
        q.register("router", 10.0)
        r = q.check("router", expected_cost=5.0)
        assert r["allowed"] is True

    def test_over_limit_blocked(self):
        """模块超配额 → 拦截"""
        q = ModuleQuota()
        q.register("router", 10.0)
        q.record_spend("router", 9.0)
        r = q.check("router", expected_cost=2.0)  # 9+2 > 10
        assert r["allowed"] is False
        assert r["over_limit"] is True

    def test_per_module_isolation(self):
        """模块间独立·一个超限不影响其他"""
        q = ModuleQuota()
        q.register("router", 10.0)
        q.register("extractor", 10.0)
        q.record_spend("router", 10.0)
        assert q.check("router")["allowed"] is False
        assert q.check("extractor")["allowed"] is True

    def test_global_limit(self):
        """全局上限兜底(跨模块总和)"""
        q = ModuleQuota(global_limit=15.0)
        q.register("a", 10.0)
        q.register("b", 10.0)
        q.record_spend("a", 10.0)
        q.record_spend("b", 4.0)
        r = q.check("b", expected_cost=2.0)  # 10+4+2=16 > 15
        assert r["allowed"] is False


class TestSummary:
    def test_summary(self):
        q = ModuleQuota(global_limit=100.0)
        q.register("router", 10.0)
        q.record_spend("router", 3.0)
        s = q.summary()
        assert s["modules"]["router"]["spent"] == 3.0
        assert s["global_spent"] == 3.0
