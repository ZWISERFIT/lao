"""P0-1 RIS Recovery Engine 前置诊断测试（2026-08-19 Shuyu审定·外部案例C）。

验证: 所有 recover 动作先读日志诊断根因→再决定重启·不盲目重试。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ris.recovery import RecoveryEngine, RecoveryAction


class TestDiagnoseBeforeRecover:
    """恢复前必须有 diagnose 步骤"""

    def test_diagnose_runs_before_recover(self):
        """diagnose_fn 被调用·且 recover 在其后"""
        order = []
        engine = RecoveryEngine()

        def detect():
            return True

        def diagnose():
            order.append("diagnose")
            return {"root_cause": "test", "skip_recovery": False}

        def recover():
            order.append("recover")
            return True

        def verify():
            order.append("verify")
            return True

        result = engine.run(
            "test_down", "test",
            detect_fn=detect, classify_fn=lambda: "test_down",
            action=RecoveryAction(name="test", diagnose_fn=diagnose,
                                  recover_fn=recover, verify_fn=verify),
        )
        assert order == ["diagnose", "recover", "verify"], \
            f"顺序错误: {order} (diagnose 必须先于 recover)"
        assert result.recovered and result.verified
        assert "diagnosis" in result.detail

    def test_diagnosis_skip_recovery(self):
        """诊断明确 skip_recovery → 不执行恢复(不盲目重试)"""
        engine = RecoveryEngine()
        recovered_called = []

        def diagnose():
            return {"root_cause": "quota_exhausted",
                    "skip_recovery": True, "reason": "429 external"}

        def recover():
            recovered_called.append(True)
            return True

        def verify():
            return True

        result = engine.run(
            "gateway_down", "gateway",
            detect_fn=lambda: True, classify_fn=lambda: "gateway_down",
            action=RecoveryAction(name="restart", diagnose_fn=diagnose,
                                  recover_fn=recover, verify_fn=verify),
        )
        assert not recovered_called, "skip_recovery 时不应执行 recover"
        assert not result.recovered
        assert "diagnosis_skip" in result.detail.get("reason", "")

    def test_quota_root_cause_detected(self):
        """日志含 429/quota → 判定 quota_exhausted(外部额度·重启无用)"""
        from unittest.mock import patch
        engine = RecoveryEngine()

        def detect():
            return True

        def diagnose():
            # 模拟 journalctl 输出含 429
            return {"root_cause": "quota_exhausted", "skip_recovery": True,
                    "reason": "quota/429: restart won't help external quota"}

        result = engine.run(
            "gateway_down", "gateway",
            detect_fn=detect, classify_fn=lambda: "gateway_down",
            action=RecoveryAction(name="restart", diagnose_fn=diagnose,
                                  recover_fn=lambda: True, verify_fn=lambda: True),
        )
        assert result.detail["diagnosis"]["root_cause"] == "quota_exhausted"
        assert result.detail["diagnosis"]["skip_recovery"] is True


class TestNoActionStillRecords:
    def test_no_action_marks_no_recovery(self):
        """无恢复动作 → 标记未恢复(不 Record 为 recovered)"""
        engine = RecoveryEngine()
        result = engine.run("x_down", "x", detect_fn=lambda: True,
                            classify_fn=lambda: "x_down", action=None)
        assert not result.recovered
        assert result.detail.get("reason") == "no_recovery_action"
