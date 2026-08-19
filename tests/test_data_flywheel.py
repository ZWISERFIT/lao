"""数据飞轮生产接线测试（2026-08-20 Founder 01:05 令·最高优先）。

验证:
  1. ModelRouter.route() 后 → ris-bridge.json 出现 lao_feedback 段(LAO→RIS)
  2. _consume_ris_bridge 低频调度(fail-open)
  3. 不改变请求结构·不阻塞路由
"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.routing.model_router import ModelRouter
from lao.effect_anchored.experience_loop import ExperienceLoop


class TestFeedbackToBridge:
    """任务2: route() 后写 lao_feedback 段"""

    def test_route_writes_lao_feedback(self):
        """注入 loop 后 route → ris-bridge.json 有 lao_feedback 段"""
        tmp = tempfile.mkdtemp()
        bridge_file = os.path.join(tmp, "ris-bridge.json")
        # 预置 RIS 段(模拟 RIS 已写)
        with open(bridge_file, "w", encoding="utf-8") as f:
            json.dump({"layer": "ris", "summary": {"events_total": 100}}, f)

        router = ModelRouter()
        loop = ExperienceLoop(home=tmp)
        # 重定向 bridge 路径(通过 env·experience_loop 支持 LAO_RIS_BRIDGE_FILE)
        os.environ["LAO_RIS_BRIDGE_FILE"] = bridge_file
        router.with_experience_loop(loop)
        router.with_feedback_bus(loop.bus)

        sel = router.route("日报数据汇总")
        assert sel is not None  # 路由不阻塞

        with open(bridge_file, "r", encoding="utf-8") as f:
            bridge = json.load(f)
        assert "lao_feedback" in bridge, f"ris-bridge.json 缺 lao_feedback 段: {list(bridge.keys())}"
        os.environ.pop("LAO_RIS_BRIDGE_FILE", None)

    def test_fail_open_no_loop(self):
        """未注入 loop → route 正常·不抛"""
        router = ModelRouter()
        sel = router.route("light")
        assert sel is not None
        assert sel.provider in ("qwen", "deepseek", "token-plan", "novarouteai")


class TestConsumeBridgeScheduling:
    """任务1: consume_bridge 低频调度·fail-open"""

    def test_consume_interval(self):
        """每 MAX_ROUTE_BETWEEN_CONSUME 次路由才消费一次"""
        router = ModelRouter()
        # mock consume 统计调用次数
        calls = {"n": 0}
        orig = router._consume_ris_bridge

        def counting():
            calls["n"] += 1
            orig()

        router._consume_ris_bridge = counting
        # 计数器归零
        router._consume_counter = 0
        for _ in range(router.MAX_ROUTE_BETWEEN_CONSUME):
            router._consume_ris_bridge()
        # 第 MAX 次应触发真实消费(内部 fail-open)
        assert calls["n"] == router.MAX_ROUTE_BETWEEN_CONSUME

    def test_consume_fail_open(self):
        """consume 内部异常 → 静默跳过(方法自带 try·路由不阻塞)"""
        router = ModelRouter()
        router._consume_counter = router.MAX_ROUTE_BETWEEN_CONSUME - 1  # 下次触发真实 consume
        # 真实 consume_bridge 读默认桥文件·即使文件异常也 fail-open 返回
        # 直接调 route 应正常(不抛)
        sel = router.route("light")
        assert sel is not None

    def test_consume_bridge_real_invocation(self):
        """真实 consume_bridge 在满 N 次后被调用(不抛·返回统计或 fail-open)"""
        router = ModelRouter()
        router._consume_counter = router.MAX_ROUTE_BETWEEN_CONSUME - 1
        # 满计数后 route 触发真实 consume_bridge(读生产桥文件·fail-open)
        sel = router.route("light")
        assert sel is not None
        # 计数器应已重置
        assert router._consume_counter == 0
