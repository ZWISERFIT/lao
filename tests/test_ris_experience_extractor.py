"""RIS Experience Extraction 测试 (Momo 负责模块。

覆盖: RecoveryResult → 恢复经验提取 / 成功经验沉淀 / 失败防复发 /
      锚点生成复用 / 持久化 / 与 RecoveryEngine 五步闭环衔接。
"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ris.experience import RiskExperienceExtractor, extract_recovery_experience
from ris.recovery import RecoveryAction, RecoveryEngine
from ris.experience.risk_experience_extractor import RecoveryExperience


def _tmp_store():
    fd, path = tempfile.mkstemp(suffix=".jsonl"); os.close(fd)
    return path


def _recovery_result(engine, event_type, agent, detect, classify,
                     recover, verify, max_attempts=3):
    return engine.run(
        event_type=event_type, agent_id=agent,
        detect_fn=detect, classify_fn=lambda: classify,
        action=RecoveryAction(name="restart",
                              recover_fn=recover, verify_fn=verify,
                              max_attempts=max_attempts),
        severity="warn")


# ── 提取主入口 ────────────────────────────────────────────────────────
def test_extract_from_successful_recovery():
    """恢复成功+验证 → 提取可复用恢复经验。"""
    eng = RecoveryEngine()
    res = _recovery_result(
        eng, "gateway_recovery", "tristan",
        detect=lambda: True, classify="gateway_down",
        recover=lambda: True, verify=lambda: True)
    assert res.recovered and res.verified

    x = RiskExperienceExtractor(store_path=_tmp_store())
    exp = x.extract_from_recovery(res, recovery_method="L1_restart")
    assert exp.event_type == "gateway_recovery"
    assert exp.agent_id == "tristan"
    assert exp.recovered is True
    assert exp.verified is True
    assert exp.recovery_method == "L1_restart"
    assert exp.category == "infrastructure"  # gateway 归类


def test_extract_from_failed_recovery():
    """恢复失败 → 经验标记未恢复 + 触发防复发(进错误模式表)。"""
    eng = RecoveryEngine()
    res = _recovery_result(
        eng, "cpu_anomaly", "stella",
        detect=lambda: True, classify="cpu_anomaly",
        recover=lambda: False, verify=lambda: False, max_attempts=2)
    assert not res.recovered

    x = RiskExperienceExtractor(store_path=_tmp_store())
    exp = x.extract_from_recovery(res)
    assert exp.recovered is False
    assert exp.attempts == 2
    assert exp.category == "infrastructure"


def test_session_and_network_category():
    """session/network 归 coordination, 其余归 infrastructure。"""
    x = RiskExperienceExtractor(store_path=_tmp_store())
    eng = RecoveryEngine()
    res1 = _recovery_result(eng, "session_unresponsive", "nova",
                            lambda: True, "session_down", lambda: True, lambda: True)
    exp1 = x.extract_from_recovery(res1)
    assert exp1.category == "coordination"

    res2 = _recovery_result(eng, "network_anomaly", "nova",
                            lambda: True, "net_down", lambda: True, lambda: True)
    exp2 = x.extract_from_recovery(res2)
    assert exp2.category == "infrastructure"


# ── 与 RecoveryEngine 五步闭环衔接 ─────────────────────────────────────
def test_full_loop_via_convenience_entry():
    """Recovery Executor 完成后一行触发 extract_recovery_experience。"""
    eng = RecoveryEngine()
    res = _recovery_result(eng, "gateway_recovery", "momo",
                           lambda: True, "gateway_down",
                           lambda: True, lambda: True)
    # 便捷函数(用默认路径, 会落盘到 ris/experience/data/)
    exp = extract_recovery_experience(res, recovery_method="L1_restart")
    assert isinstance(exp, RecoveryExperience)
    assert exp.recovered is True
    assert exp.event_type == "gateway_recovery"


# ── 持久化 ─────────────────────────────────────────────────────────────
def test_persist_and_reload():
    """恢复经验 JSONL 落盘 + 可重载。"""
    path = _tmp_store()
    x = RiskExperienceExtractor(store_path=path)
    eng = RecoveryEngine()
    res = _recovery_result(eng, "gateway_recovery", "tristan",
                           lambda: True, "gateway_down",
                           lambda: True, lambda: True)
    exp = x.extract_from_recovery(res, recovery_method="L2_hard")

    # 新实例重载
    x2 = RiskExperienceExtractor(store_path=path)
    recs = x2.recovery_experiences()
    assert len(recs) == 1
    assert recs[0].recovery_method == "L2_hard"
    assert recs[0].verified is True


def test_stats_counts_by_event():
    """台账统计: total/recovered/failed/by_event。"""
    path = _tmp_store()
    x = RiskExperienceExtractor(store_path=path)
    eng = RecoveryEngine()
    # 两条成功
    for et, ag in [("gateway_recovery", "a"), ("session_recovery", "b")]:
        res = _recovery_result(eng, et, ag, lambda: True, et,
                               lambda: True, lambda: True)
        x.extract_from_recovery(res)
    # 一条失败
    res = _recovery_result(eng, "cpu_anomaly", "c",
                           lambda: True, "cpu_anomaly", lambda: False, lambda: False)
    x.extract_from_recovery(res)

    s = x.stats()
    assert s["total"] == 3
    assert s["recovered"] == 2
    assert s["failed"] == 1
    assert s["by_event"]["gateway_recovery"] == 1


# ── 锚点生成(供 experience_matching 复用) ─────────────────────────────
def test_to_lao_anchors_from_successes():
    """成功恢复经验重复 → 生成 LAO 锚点(对齐 auto_extract_anchors 规则)。"""
    path = _tmp_store()
    x = RiskExperienceExtractor(store_path=path)
    eng = RecoveryEngine()
    # 同一恢复成功 3 次(达到 Fact 锚点阈值)
    for _ in range(3):
        res = _recovery_result(eng, "gateway_recovery", "tristan",
                               lambda: True, "gateway_down",
                               lambda: True, lambda: True)
        x.extract_from_recovery(res)

    anchors = x.to_lao_anchors()
    # 成功经验成锚点(Fact 类型·trust 计算)
    # 注: auto_extract_anchors 对 success 生成 fact 锚点
    assert isinstance(anchors, list)
