# v3.5.2-laorefactor: R4
"""R4.2 LAO→RIS 反哺测试（PRD v1.1 第5节任务3·2026-08-19 双失联根治）。

新增用例: l3_feedback_to_bridge() — LAO 路由结果(provider/model/命中率/成本)
写入 ris-bridge.json 的 lao_feedback 段·且保留 RIS 写入的其他段(read-modify-write)。
铁律验证: 只写共享 JSON·不 import ris 包·测试零生产副作用(全部临时目录)。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.experience_loop import ExperienceLoop  # noqa: E402


@pytest.fixture()
def loop(tmp_path):
    return ExperienceLoop(home=str(tmp_path / "loop-home"))


@pytest.fixture()
def bridge_file(tmp_path):
    return str(tmp_path / "ris-bridge.json")


@pytest.fixture()
def experience_store(tmp_path, monkeypatch):
    """经验库重定向到临时路径(命中率/成本数据源·不读生产库)。"""
    store = str(tmp_path / "lao_experiences.jsonl")
    monkeypatch.setenv("LAO_EXPERIENCE_STORE", store)
    monkeypatch.setenv("LAO_L3_OUT_DIR", str(tmp_path / "fanout-out"))
    monkeypatch.delenv("LAO_RIS_BRIDGE_FILE", raising=False)
    return store


def _write_experience(store, provider, cache_hit, cost):
    rec = {"experience_id": f"exp-{provider}-{cost}", "provider_used": provider,
           "cache_hit": cache_hit, "actual_cost": cost}
    with open(store, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def test_l3_feedback_to_bridge_writes_lao_feedback_section(
        loop, bridge_file, experience_store):
    """LAO 路由后 ris-bridge.json 出现 lao_feedback 段·含最近路由统计(PRD验收)。

    覆盖: provider/model(路由统计) + 命中率(hit_rate) + 成本(cost) +
    RIS 段保留 + fanout 桥接线(bridge_file 显式传入时反哺)。
    """
    # 预置 RIS 写入的段 → 验证 read-modify-write 只更新 lao_feedback·不破坏其他键
    ris_section = {
        "layer": "ris", "schema_version": "1.0",
        "summary": {"isolated_providers": ["novarouteai"],
                    "provider_status": {"token-plan": "down"}},
        "recent_events": [{"event_type": "gateway_down", "ts": "t1"}],
    }
    with open(bridge_file, "w", encoding="utf-8") as f:
        json.dump(ris_section, f, ensure_ascii=False)

    # LAO 路由结果回流(2 成功 + 1 失败·含 429 冲突错误)
    loop.record_route_result("qwen", "qwen3.7-flash", True)
    loop.record_route_result("qwen", "qwen3.7-flash", True)
    loop.record_route_result("novarouteai", "glm-5.2", False, "HTTP 429 quota exhausted")

    # 经验库(命中率/成本数据源): 2 命中 1 未命中·成本 0.01/0.02
    _write_experience(experience_store, "qwen", True, 0.01)
    _write_experience(experience_store, "qwen", True, 0.0)
    _write_experience(experience_store, "novarouteai", False, 0.02)

    res = loop.l3_feedback_to_bridge(bridge_file=bridge_file)
    assert res["ok"] is True

    with open(bridge_file, encoding="utf-8") as f:
        bridge = json.load(f)
    # RIS 段原样保留(物理隔离: LAO 只动自己的段)
    assert bridge["layer"] == "ris"
    assert bridge["summary"]["isolated_providers"] == ["novarouteai"]
    assert bridge["recent_events"] == [{"event_type": "gateway_down", "ts": "t1"}]

    # lao_feedback 段: provider/model/命中率/成本(PRD R4.2 四要素)
    fb = bridge["lao_feedback"]
    assert fb["layer"] == "lao"
    stats = fb["route_stats"]
    assert stats["total"] == 3          # 冲突派生事件不计入(去重)
    assert stats["success"] == 2
    assert stats["failed"] == 1
    assert stats["success_rate"] == round(2 / 3, 4)
    assert stats["by_provider"]["qwen"] == {
        "total": 2, "success": 2, "models": {"qwen3.7-flash": 2}}
    assert stats["by_provider"]["novarouteai"]["success"] == 0
    # 最近路由含失败明细
    failed = [r for r in fb["recent_routes"] if not r["success"]]
    assert len(failed) == 1
    assert failed[0]["provider"] == "novarouteai"
    assert failed[0]["model"] == "glm-5.2"
    assert "429" in failed[0]["error"]
    # 命中率(经验库 cache_hit) + 成本(actual_cost·按 provider)
    assert fb["hit_rate"] == round(2 / 3, 4)
    assert fb["cost"]["total_actual_cost"] == pytest.approx(0.03)
    assert fb["cost"]["by_provider"]["qwen"] == pytest.approx(0.01)
    assert fb["cost"]["by_provider"]["novarouteai"] == pytest.approx(0.02)

    # fanout 桥接线: bridge_file 显式传入 → 反哺结果进 fanout 输出
    out = loop.l3_route_result_fanout(out_dir=os.path.dirname(experience_store),
                                      bridge_file=bridge_file)
    assert out["bridge_feedback"]["ok"] is True
    with open(bridge_file, encoding="utf-8") as f:
        assert "lao_feedback" in json.load(f)


def test_l3_feedback_to_bridge_fail_open(loop, tmp_path):
    """桥路径不可写 → fail-open 返回 ok=False·不抛错(绝不阻塞路由)。"""
    blocker = str(tmp_path / "blocker")
    with open(blocker, "w") as f:
        f.write("not-a-dir")
    res = loop.l3_feedback_to_bridge(bridge_file=str(blocker + "/sub/ris-bridge.json"))
    assert res["ok"] is False


if __name__ == "__main__":
    import unittest
    unittest.main()
