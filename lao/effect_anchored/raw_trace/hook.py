# -*- coding: utf-8 -*-
"""raw_trace.hook · 确权产卡挂点的守护封装（A2 双写接入）

接入口径：experience_loop._confirm_one 确权成功后调用本模块；
默认关闭时（无授权记录）立即静默返回，对现役流程零影响、零写入。
调用方一律 try/except 包裹：本模块任何异常不得破坏确权主流程。
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def register_confirmed_anchor(anchor_id: str, owner: str,
                              payload: Dict[str, Any],
                              card_file: Optional[str] = None,
                              vault_root: Optional[str] = None) -> Dict[str, Any]:
    """确权成功后：原卷留一条；若知卡对象文件，则同步双写登记。

    Returns: {"logged": bool, "registered": bool}——全部失败亦返回
    {"logged": False, "registered": False}，不抛给调用方。
    """
    result = {"logged": False, "registered": False}
    try:
        from lao.effect_anchored.raw_trace.consent import RawTraceConsent
        from lao.effect_anchored.raw_trace.vault import RawTraceVault

        vault = RawTraceVault(vault_root)
        consent = RawTraceConsent(vault.vault_root)
        rec = consent.active()
        if rec is None:
            return result  # 默认关闭：零影响直返
        vault.append(
            kind="l3_confirmation",
            payload={
                "anchor_id": anchor_id,
                "owner": owner,
                "anchor": payload,
            },
            source="experience_loop._confirm_one",
        )
        result["logged"] = True

        if card_file:
            from lao.effect_anchored.raw_trace.registry import ReferenceRegistry

            registry = ReferenceRegistry(vault.vault_root)
            registry.register(
                card_id=anchor_id,
                version=1,
                subject_id="did:zwf:%s" % owner,
                library="collaborative",
                ownership=owner,
                visibility="internal",
                origin_path=card_file,
                revenue_formula="user_50_platform_50",
                actor=owner,
            )
            result["registered"] = True
    except Exception:
        # 守护纪律：本模块异常不上抛，不破坏确权主流程
        pass
    return result
