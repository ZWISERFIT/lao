"""P0-2 LAO key_anchor 循环边界哨兵测试（2026-08-19 Shuyu审定·外部案例B）。

验证: 文本重复哈希检测·同一文本连续N次触发→判定循环→拦截(超阈值)。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.loop_guard import LoopGuard


class TestLoopDetection:
    """模拟循环文本 → 拦截"""

    def test_under_threshold_no_intercept(self):
        """低于阈值(2次<3) → 不拦截"""
        g = LoopGuard(threshold=3)
        r1 = g.check("相同的循环文本")
        r2 = g.check("相同的循环文本")
        assert not r1["looping"]
        assert not r2["looping"]
        assert r2["count"] == 2

    def test_threshold_intercepts(self):
        """连续3次 → 拦截(looping=True)"""
        g = LoopGuard(threshold=3)
        for i in range(3):
            r = g.check("死循环文本")
        assert r["looping"] is True
        assert r["count"] == 3
        assert r["intercepted"] is True

    def test_different_text_not_intercepted(self):
        """不同文本 → 各自独立计数·不误伤"""
        g = LoopGuard(threshold=3)
        g.check("文本A")
        g.check("文本B")
        g.check("文本A")
        r = g.check("文本B")
        assert not r["looping"]  # 各自2次·未超3

    def test_reset_clears_count(self):
        """正常处理后 reset → 计数清零·不误伤后续"""
        g = LoopGuard(threshold=3)
        g.check("正常请求")
        g.check("正常请求")
        g.reset("正常请求")
        r = g.check("正常请求")
        assert not r["looping"]
        assert r["count"] == 1

    def test_per_agent_isolation(self):
        """per-agent 隔离: 不同 agent 各自计数"""
        g1 = LoopGuard(threshold=2, agent_id="agent-a")
        g2 = LoopGuard(threshold=2, agent_id="agent-b")
        g1.check("重复文本")
        r = g2.check("重复文本")  # agent-b 第一次 → 不拦截
        assert not r["looping"]
        r2 = g2.check("重复文本")  # agent-b 第二次 → 拦截
        assert r2["looping"]


class TestFailOpen:
    def test_empty_text_ok(self):
        """空文本 → 放行不拦截"""
        g = LoopGuard()
        r = g.check("")
        assert not r["looping"]

    def test_summary(self):
        """summary 统计"""
        g = LoopGuard()
        g.check("A")
        g.check("B")
        s = g.summary()
        assert s["active_fingerprints"] == 2
        assert s["total_events"] == 2
