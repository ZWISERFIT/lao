# -*- coding: utf-8 -*-
"""raw_trace · 后训练第一批原卷留档管道（99号批件 A1＋A2＋D1）

依据：101号签批件（443474c6…）全批；99号批件；一页施工方案（呈核件，哈希 f68e9d9b…）。
对齐：件②记忆容器登记口径（113号呈验件，114号终审PASS）——
    对象文件＋manifest＋SHA-256、三态 candidate/ratified/withdrawn、
    流转账承载状态变更、只存指向与哈希不复制正文。

三条纪律边界（寸步不让）：
    1. 平台选择＝偏好来源：本模块不做任何配对采集，只留原始轨迹原卷；
    2. 零远程遥测：本模块不发起任何网络出站调用（断网测试为验收硬门）；
    3. 数据留用户侧：全部写盘限于 vault_root（默认用户本机 LAO_HOME/raw_trace），
       不建任何回传通道。

默认关闭：无授权记录时，任何写入（原卷账、索引、登记、审计）一律拒绝。
"""

from lao.effect_anchored.raw_trace.consent import RawTraceConsent, ConsentDenied
from lao.effect_anchored.raw_trace.redaction import redact_payload
from lao.effect_anchored.raw_trace.vault import RawTraceVault
from lao.effect_anchored.raw_trace.registry import (
    ReferenceRegistry,
    ReferenceLedgerError,
)
from lao.effect_anchored.raw_trace.withdraw import WithdrawalGateway

__all__ = [
    "RawTraceConsent",
    "ConsentDenied",
    "redact_payload",
    "RawTraceVault",
    "ReferenceRegistry",
    "ReferenceLedgerError",
    "WithdrawalGateway",
]
