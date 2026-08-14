"""RIS Recovery Engine 测试(Phase 2·五步闭环·铁律: 禁止只restart不验证)。

Detect → Classify → Recover → Verify → Record
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ris.recovery import RecoveryEngine, RecoveryAction


def test_no_anomaly_no_recovery():
    """无异常 → 不需要恢复(不产生 record)。"""
    eng = RecoveryEngine()
    r = eng.run("session_recovery", "tristan", detect_fn=lambda: False)
    assert r.recovered is False
    assert r.attempts == 0
    assert len(eng.records()) == 0  # 无异常不记录(无恢复动作发生)


def test_full_cycle_recovered_and_verified():
    """完整闭环: Detect→Classify→Recover→Verify→Record(recovered+verified)。"""
    eng = RecoveryEngine()
    r = eng.run(
        "session_recovery", "tristan",
        detect_fn=lambda: True,                 # 检测到异常
        classify_fn=lambda: "session_down",     # 分类
        action=RecoveryAction(name="restart_session",
                              recover_fn=lambda: True,   # 恢复成功
                              verify_fn=lambda: True),   # 验证成功(铁律)
    )
    assert r.recovered is True
    assert r.verified is True
    assert r.recorded is True
    assert r.classified == "session_down"
    assert r.attempts == 1
    ev = eng.records()[-1]
    assert ev.status == "recovered"


def test_restart_without_verify_not_recorded():
    """铁律负测试: 只 restart 不验证(verify_fn=None) → 不 Record 为 recovered。"""
    eng = RecoveryEngine()
    r = eng.run(
        "gateway_recovery", "gateway",
        detect_fn=lambda: True,
        action=RecoveryAction(name="restart_gateway",
                              recover_fn=lambda: True,   # restart 成功
                              verify_fn=None),           # ❌ 不验证
    )
    assert r.recovered is False   # 禁止只 restart 不验证
    assert r.verified is False
    assert r.recorded is False
    ev = eng.records()[-1]
    assert ev.status == "failed"


def test_verify_fails_then_retries():
    """Verify 失败 → 重试(有 budget·不无限循环)。"""
    eng = RecoveryEngine()
    calls = {"n": 0}
    def recover():
        calls["n"] += 1
        return True
    def verify():
        return calls["n"] >= 3   # 前2次验证失败·第3次成功
    r = eng.run(
        "session_recovery", "nova",
        detect_fn=lambda: True,
        action=RecoveryAction(name="restart", recover_fn=recover,
                              verify_fn=verify, max_attempts=3),
    )
    assert calls["n"] == 3      # 重试3次
    assert r.recovered is True
    assert r.verified is True
    assert r.recorded is True


def test_exhaust_attempts_not_recovered():
    """重试耗尽(budget 上限) → 不 recovered。"""
    eng = RecoveryEngine()
    r = eng.run(
        "cpu_anomaly", "stella",
        detect_fn=lambda: True,
        action=RecoveryAction(name="restart",
                              recover_fn=lambda: False,  # 恢复一直失败
                              verify_fn=lambda: True, max_attempts=2),
    )
    assert r.recovered is False
    assert r.attempts == 2      # 尝试2次后放弃
    assert r.recorded is False


def test_no_action_not_recovered():
    """无恢复动作 → 标记未恢复(不 Record 为 recovered)。"""
    eng = RecoveryEngine()
    r = eng.run("provider_isolation", "momo", detect_fn=lambda: True)
    assert r.recovered is False
    assert r.recorded is False
    assert r.detail.get("reason") == "no_recovery_action"
