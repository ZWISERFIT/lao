"""
Consent Integration — LAO v3.1 P1-4
====================================

4 处集成调用点的授权接线（P1·尽快修复派发）。

在关键动作前检查四阶段授权(consent_gate.FOUR_STAGES):
  ① cost      : Router 模型路由前 → 检查「成本追踪」授权
  ③ upload    : Ethan 经验上传前 → 检查「经验上传评估」授权
  ④ trade     : Melody 确认交易前 → 检查「确权交易」授权
  ③ + ④      : Factory 生产经验时 → 检查「上传 + 交易」授权

用法(在对应集成点调用):
  gate = FourStageConsent()
  ok = guard_route(gate, owner)        # False=未授权·应阻止路由
  ok = guard_upload(gate, owner, exp)  # False=未授权·应阻止上传Ethan
  ok = guard_trade(gate, owner, price) # False=未授权·应阻止Melody交易
  ok = guard_factory(gate, owner)      # False=未授权·应阻止生产/上传/交易

每个 guard 返回 (granted, reason): 明确未授权原因, 供调用方反馈给用户。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from lao.effect_anchored.consent_gate import FourStageConsent


def _stage_gate(consent: FourStageConsent, stage: str,
                owner: str, domain: str) -> Tuple[bool, str]:
    """通用: 检查某 stage 是否授权。"""
    granted = consent.is_stage_granted(stage, owner, domain)
    labels = {s["id"]: s["label"] for s in getattr(consent, "list_stages", lambda: [])()}
    label = labels.get(stage, stage)
    if granted:
        return True, "已授权"
    return False, f"未授权「{label}」·需先确认"


def guard_route(consent: FourStageConsent, owner: str,
                domain: str = "routing") -> Tuple[bool, str]:
    """① 模型路由前: 检查成本追踪授权。"""
    return _stage_gate(consent, "cost", owner, domain)


def guard_upload(consent: FourStageConsent, owner: str,
                 experience_id: str = "",
                 domain: str = "experience") -> Tuple[bool, str]:
    """③ 经验上传 Ethan 前: 检查「经验上传」授权。"""
    return _stage_gate(consent, "upload", owner, domain)


def guard_trade(consent: FourStageConsent, owner: str,
                price: Optional[float] = None,
                domain: str = "melody") -> Tuple[bool, str]:
    """④ Melody 确权交易前: 检查「确权交易」授权。"""
    return _stage_gate(consent, "trade", owner, domain)


def guard_factory(consent: FourStageConsent, owner: str,
                  domain: str = "experience") -> Tuple[bool, str]:
    """Factory 生产经验时: 需「上传(经验域) + 交易(Melody域)」双授权。"""
    up_ok, up_reason = _stage_gate(consent, "upload", owner, "experience")
    if not up_ok:
        return False, up_reason
    tr_ok, tr_reason = _stage_gate(consent, "trade", owner, "melody")
    if not tr_ok:
        return False, tr_reason
    return True, "已授权(上传+交易)"
