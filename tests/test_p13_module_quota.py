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


class TestGuardrail3:
    """护栏3: 配额 = 历史峰值日均 × 150% 裕度(不可设均值·防误伤峰值)"""

    def test_suggest_from_peak(self):
        # 峰值日均 10·均值 5 → 建议 = max(10) × 1.5 = 15
        limit = ModuleQuota.suggest_limit_from_history(avg_daily=5.0, peak_daily=10.0)
        assert limit == 15.0

    def test_suggest_falls_back_to_avg(self):
        # 无峰值数据 → 用均值 × 1.5(同裕度)
        limit = ModuleQuota.suggest_limit_from_history(avg_daily=8.0, peak_daily=0.0)
        assert limit == 12.0

    def test_peak_not_under_quota(self):
        """峰值日均不超过配额(裕度保护峰值)"""
        limit = ModuleQuota.suggest_limit_from_history(avg_daily=5.0, peak_daily=10.0)
        assert limit >= 10.0  # 峰值在配额内

    def test_peak_alone_not_below_avg(self):
        limit = ModuleQuota.suggest_limit_from_history(avg_daily=20.0, peak_daily=10.0)
        assert limit >= 20.0  # 至少覆盖均值
