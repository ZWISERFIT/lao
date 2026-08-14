"""P1-key 测试: lao-router 按 Agent 分发独立 key(治本·解决共用Tristan key的B1盲点)。

智囊团决议 P1: per-Agent 独立 key·DeepSeek 后台见各 Agent 独立用量。
"""
import sys, os, importlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "lao", "effect_anchored", "routing"))

from lao.effect_anchored.routing import lao_router_server as m


def test_extract_agent_from_model_hint():
    """从 model_hint 前缀提取 Agent 名。"""
    cases = {
        "deepseek-momo/deepseek-v4-flash": "momo",
        "deepseek-tristan/deepseek-v4-pro": "tristan",
        "deepseek-zeus/deepseek-v4-flash": "zeus",
        "deepseek-stella/deepseek-v4-pro": "stella",
        "deepseek-shuyu/deepseek-v4-pro": "shuyu",
        "deepseek-nova/deepseek-v4-flash": "nova",
        "deepseek-baron/deepseek-v4-flash": "baron",
        "deepseek-ethan/deepseek-v4-flash": "ethan",
        "deepseek-luna/deepseek-v4-flash": "luna",
    }
    for hint, expect in cases.items():
        got = m._extract_agent(hint, {})
        assert got == expect, f"{hint}: {got} != {expect}"


def test_extract_agent_from_header():
    """优先从 x-lao-agent header 提取。"""
    assert m._extract_agent("", {"x-lao-agent": "zeus"}) == "zeus"
    assert m._extract_agent("", {}) == ""


def test_nine_agent_keys_available():
    """9 Agent 独立 key 全部就绪(secrets.env)。"""
    keys = m.AGENT_KEYS
    assert len(keys) == 9
    for agent, key in keys.items():
        assert key, f"{agent} 的 key 为空"


def test_unknown_agent_falls_back():
    """未知 agent → 空(回退默认 deepseek key)。"""
    assert m._extract_agent("deepseek-unknown/model", {}) == ""


def test_distinct_keys():
    """各 Agent key 各不相同(独立·非共用)。"""
    vals = list(m.AGENT_KEYS.values())
    assert len(set(vals)) == 9  # 9 个 distinct key
