"""Phase1 Step2 测试: TrustEvent 唯一事件骨架 (subtype + domain + ledger)。

创始人终审 2026-08-13 P0-2: TrustEvent 成为全系统唯一事件骨架, 消灭多账本。
六类 subtype: DecisionEvent / ContextEvent / RuntimeEvent / RecoveryEvent / ExperienceEvent / OwnershipEvent
"""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.schema import TrustEvent, make_event, TrustEventLedger

# 六类合法 subtype
SUBTYPES = ["DecisionEvent", "ContextEvent", "RuntimeEvent",
            "RecoveryEvent", "ExperienceEvent", "OwnershipEvent"]


def test_trustevent_six_subtypes():
    """TrustEvent 必须支持六类 subtype 作为统一骨架。"""
    for s in SUBTYPES:
        e = TrustEvent(event_id=f"E-{s}-001", date="2026-08-13", type="decision",
                       failure="", subtype=s, domain="gateway")
        assert e.subtype == s
        assert e.domain == "gateway"


def test_make_event_with_subtype_and_domain():
    """make_event 必须透传 subtype/domain 并写入账本(hash+verified)。"""
    tmp = tempfile.mktemp(suffix=".json")
    ledger = TrustEventLedger(path=tmp)
    ev = make_event(agent="STELLA", etype="failure", description="compaction abnormal",
                    evidence="ctx=117K", ledger=ledger,
                    subtype="ContextEvent", domain="context")
    assert ev.subtype == "ContextEvent"
    assert ev.domain == "context"
    assert ev.hash  # 账本填充 hash
    assert ev.verified is True
    # 持久化确认
    reloaded = ledger.load()
    assert len(reloaded) == 1
    assert reloaded[0].subtype == "ContextEvent"
    os.remove(tmp)


def test_trustevent_is_single_backbone():
    """唯一骨架: 任何事件都能以 TrustEvent subtype 表达, 不另起账本。"""
    # 六类事件统一用 make_event 写入同一账本(消灭多账本)
    tmp = tempfile.mktemp(suffix=".json")
    ledger = TrustEventLedger(path=tmp)
    for i, s in enumerate(SUBTYPES):
        make_event(agent="TRISTAN", etype="info", description=f"evt {i}",
                   evidence=f"d={i}", ledger=ledger, subtype=s, domain="bridge")
    assert len(ledger.load()) == 6
    assert set(e.subtype for e in ledger.load()) == set(SUBTYPES)
    os.remove(tmp)
