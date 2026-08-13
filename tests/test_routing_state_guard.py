"""LAO v3.4 Production Routing Stabilization · RoutingStateGuard 测试 (Phase3)。

防误回滚: 任何 provider/baseUrl 修改必须产生 RoutingChangeEvent(before/after/actor/reason/approval)。
"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.routing_state_guard import RoutingStateGuard


def _mk_config(providers: dict) -> str:
    """造一个临时 openclaw.json(含 models.providers)。"""
    cfg = {"models": {"providers": {}}}
    for k, base in providers.items():
        cfg["models"]["providers"][k] = {"baseUrl": base}
    f = tempfile.mktemp(suffix=".json")
    json.dump(cfg, open(f, "w"))
    return f


def test_snapshot_extracts_baseurls():
    """snapshot() 提取 provider/baseUrl 映射。"""
    f = _mk_config({"deepseek-stella": "http://127.0.0.1:8765/v1",
                    "deepseek-zeus": "https://api.deepseek.com/v1"})
    guard = RoutingStateGuard(config_path=f)
    snap = guard.snapshot()
    assert snap["deepseek-stella"] == "http://127.0.0.1:8765/v1"
    assert snap["deepseek-zeus"] == "https://api.deepseek.com/v1"
    os.remove(f)


def test_detect_change_emits_event():
    """快照后改 baseUrl → detect_change 产生 RoutingChangeEvent。"""
    f = _mk_config({"deepseek-stella": "https://api.deepseek.com/v1"})
    guard = RoutingStateGuard(config_path=f, ledger_path="/tmp/rce-test2.jsonl")
    guard.save_snapshot()
    # 改 baseUrl
    cfg = json.load(open(f)); cfg["models"]["providers"]["deepseek-stella"]["baseUrl"] = "http://127.0.0.1:8765/v1"
    json.dump(cfg, open(f, "w"))
    ev = guard.detect_change(actor="tristan", reason="restore lao-router", approval="founder")
    assert ev is not None
    assert "deepseek-stella" in ev.changed_agents
    assert ev.before["deepseek-stella"] == "https://api.deepseek.com/v1"
    assert ev.after["deepseek-stella"] == "http://127.0.0.1:8765/v1"
    os.remove(f)


def test_no_change_no_event():
    """无变化 → detect_change 返回 None(不产生噪音)。"""
    f = _mk_config({"deepseek-stella": "http://127.0.0.1:8765/v1"})
    guard = RoutingStateGuard(config_path=f, ledger_path="/tmp/rce-test3.jsonl")
    guard.save_snapshot()
    ev = guard.detect_change()
    assert ev is None
    os.remove(f)


def test_trust_event_fields():
    """RoutingChangeEvent → TrustEvent(含 before/after/actor/reason/approval)。"""
    f = _mk_config({"deepseek-zeus": "https://api.deepseek.com/v1"})
    guard = RoutingStateGuard(config_path=f, ledger_path="/tmp/rce-test4.jsonl")
    guard.save_snapshot()
    cfg = json.load(open(f)); cfg["models"]["providers"]["deepseek-zeus"]["baseUrl"] = "http://127.0.0.1:8765/v1"
    json.dump(cfg, open(f, "w"))
    ev = guard.detect_change(actor="zeus", reason="cutover", approval="founder")
    te = ev.to_trust_event()
    assert te["event"] == "RoutingChange"
    assert te["subtype"] == "RuntimeEvent"
    assert te["actor"] == "zeus"
    assert te["reason"] == "cutover"
    assert te["approval"] == "founder"
    os.remove(f)


def test_verify_routing_9_agents():
    """verify_routing: 校验 9 agent 是否都指向预期 baseUrl。"""
    providers = {f"deepseek-{a}": "http://127.0.0.1:8765/v1"
                 for a in ["stella", "zeus", "tristan", "nova", "momo", "shuyu", "baron", "ethan", "luna"]}
    f = _mk_config(providers)
    guard = RoutingStateGuard(config_path=f)
    v = guard.verify_routing("http://127.0.0.1:8765/v1", 9)
    assert v["ok"] is True
    assert v["on_router"] == 9
    os.remove(f)
