# v3.5.2-laorefactor: R2.2
"""R2.2 方案A强化测试（PRD v1.1 第5节任务1·2026-08-19 双失联根治）。

覆盖3用例:
  1. 方案A降级: token-plan 耗尽 + 池内有同 provider flash 档(qwen3.6-flash)
     → 降级到 flash·不换 provider·审计标记 quota_degrade_flash
  2. 方案C换provider: token-plan 耗尽 + 同 provider 无 flash 档
     → 剔除 token-plan 换 provider·审计标记 quota_failover_provider
  3. 不空转: token-plan 耗尽 + 全池皆 token-plan 且无 flash 档
     → 保底原池·路由仍返回有效选路(不抛错不空转)

硬性约束验证: 降级不改变请求结构·不中途改道(路由在构建阶段决策)。
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.routing.model_router import ModelRouter, RouteSelection  # noqa: E402


def _entry(model, provider, quality=0.76, latency=0.55, cost="$0.08/$0.20"):
    return {"model": model, "provider": provider, "credit": False,
            "quality": quality, "latency": latency, "cost": cost}


# route() 的 .get(tier, MODEL_POOL["medium"]) 急切求值 default → 池必须含 "medium" 键
def _with_medium(pool):
    out = dict(pool)
    out.setdefault("medium", list(pool.values())[0])
    return out


# 方案A场景池: token-plan 首选 qwen3.8-max(非flash) + 同provider flash 档 + 其他provider
POOL_PLAN_A = _with_medium({
    "light": [
        _entry("qwen3.8-max", "token-plan", quality=0.85, latency=0.6, cost="$0.50/$1.20"),
        _entry("qwen3.6-flash", "token-plan", quality=0.76, latency=0.55, cost="$0.08/$0.20"),
        _entry("qwen-plus", "qwen", quality=0.85, latency=0.5, cost="$0.05/$0.10"),
    ],
})

# 方案C场景池: token-plan 只有非 flash 档·无同 provider flash 可降级
POOL_PLAN_C = _with_medium({
    "light": [
        _entry("qwen3.8-max", "token-plan", quality=0.85, latency=0.6, cost="$0.50/$1.20"),
        _entry("deepseek-v4-flash", "deepseek", quality=0.70, latency=0.3, cost="$0.14/$0.28"),
    ],
})

# 不空转场景池: 全池皆 token-plan·且无 flash 档
POOL_ALL_DEAD = _with_medium({
    "light": [
        _entry("qwen3.8-max", "token-plan", quality=0.85, latency=0.6, cost="$0.50/$1.20"),
        _entry("glm-5.2", "token-plan", quality=0.78, latency=0.55, cost="$0.08/$0.20"),
    ],
})


class QuotaFailoverTestBase(unittest.TestCase):
    """公共夹具: 隔离 openclaw 配额/网络信号·强制 token-plan 耗尽。"""

    def setUp(self):
        # 强制配额信号2(env)·不依赖仓库内 provider-quota.json 的当前状态
        self._orig_env = os.environ.get("PROVIDER_QUOTA_TOKEN_PLAN")
        os.environ["PROVIDER_QUOTA_TOKEN_PLAN"] = "exhausted"
        # 重定向 CONFIG_PATH → 动态池/fail-fast 不读真实 openclaw.json(测试池自治)
        self._orig_config_path = ModelRouter.CONFIG_PATH
        ModelRouter.CONFIG_PATH = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "__no_such_openclaw__.json")

    def tearDown(self):
        if self._orig_env is None:
            os.environ.pop("PROVIDER_QUOTA_TOKEN_PLAN", None)
        else:
            os.environ["PROVIDER_QUOTA_TOKEN_PLAN"] = self._orig_env
        ModelRouter.CONFIG_PATH = self._orig_config_path

    def _make_router(self, pool):
        r = ModelRouter(model_pool=pool)
        r.MODEL_POOL = pool          # 双保险: 覆盖任何动态池残留
        r._verify_model_exists = MagicMock(return_value=True)  # 隔离网络探测
        r._switch_auditor = MagicMock()                        # 捕获审计标记
        return r

    def _audit_reasons(self, router):
        return [c.args[0].reason for c in router._switch_auditor.record.call_args_list
                if c.args]


class TestPlanADegradeFlash(QuotaFailoverTestBase):
    """用例1: 方案A — token-plan 耗尽 → 同 provider 降级 flash·不换 provider。"""

    def test_quota_degrade_flash_same_provider(self):
        """baron(绑定token-plan) + token-plan 耗尽 + 池内有 qwen3.6-flash
        → 选 token-plan/qwen3.6-flash·provider 不变·审计 quota_degrade_flash。"""
        r = self._make_router(POOL_PLAN_A)
        sel = r.route("light", agent="baron")
        self.assertIsInstance(sel, RouteSelection)
        # 方案A: 不换 provider·降级到同 provider flash 档
        self.assertEqual(sel.provider, "token-plan",
                         "方案A: 同 provider 降级·不得换 provider")
        self.assertEqual(sel.model, "qwen3.6-flash",
                         "方案A: 应降级到 flash 档(qwen3.6-flash)")
        # 审计标记: quota_degrade_flash(非 quota_failover_provider)
        reasons = self._audit_reasons(r)
        self.assertIn("quota_degrade_flash", reasons,
                      f"审计应含 quota_degrade_flash·实际: {reasons}")
        self.assertNotIn("quota_failover_provider", reasons)
        # from→to 轨迹: qwen3.8-max → qwen3.6-flash(同 provider·降级不换道)
        entry = r._switch_auditor.record.call_args_list[0].args[0]
        self.assertEqual(entry.from_provider, "token-plan")
        self.assertEqual(entry.from_model, "qwen3.8-max")
        self.assertEqual(entry.to_model, "qwen3.6-flash")


class TestPlanCFailoverProvider(QuotaFailoverTestBase):
    """用例2: 方案C — 同 provider 无 flash 档 → 剔除换 provider。"""

    def test_quota_failover_provider_switch(self):
        """baron + token-plan 耗尽 + token-plan 无 flash 档
        → 换 provider(非token-plan)·审计 quota_failover_provider。"""
        r = self._make_router(POOL_PLAN_C)
        sel = r.route("light", agent="baron")
        self.assertIsInstance(sel, RouteSelection)
        # 方案C: 换 provider·不再选 token-plan
        self.assertNotEqual(sel.provider, "token-plan",
                             "方案C: 应剔除耗尽的 token-plan 换 provider")
        self.assertEqual(sel.provider, "deepseek")
        reasons = self._audit_reasons(r)
        self.assertIn("quota_failover_provider", reasons,
                      f"审计应含 quota_failover_provider·实际: {reasons}")
        self.assertNotIn("quota_degrade_flash", reasons)


class TestNoSpin(QuotaFailoverTestBase):
    """用例3: 不空转 — 全池皆耗尽 provider 且无 flash 档 → 保底原池。"""

    def test_all_dead_pool_still_routes(self):
        """全池皆 token-plan(耗尽)且无 flash 档 → 路由仍返回有效选路·不抛错。"""
        r = self._make_router(POOL_ALL_DEAD)
        sel = r.route("light", agent="baron")   # 不得抛错·不得返回 None
        self.assertIsInstance(sel, RouteSelection)
        self.assertTrue(sel.model)
        self.assertTrue(sel.provider)
        # 保底原池: 仍从 token-plan 池选(不空转·绝不无路可走)
        self.assertEqual(sel.provider, "token-plan")


if __name__ == "__main__":
    unittest.main()
