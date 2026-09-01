# P0 LAO成本防线: 规格书 _rev2 (SHA-256 4d6bc3aa…17c4) 施工件
"""LAO 成本防线 (P0) — S1~S5 模块包。

铁律: ① 止血基线 model_router.py.bak-hemostasis-20260830 不回退;
      ② 成本数字上报前官方后台对账(N-01); ③ 卡顿即停手上报。
本包全部为只读旁路实现: 不修改选路逻辑、不写真实账本。
"""

from .model import (  # noqa: F401
    Alert,
    CostEvent,
    SwitchEvent,
    load_cost_events,
    load_switch_events,
)
from .router import (  # noqa: F401
    AlertOutbox,
    DeliveryStatus,
    classify,
    decide_escalation,
    needs_reconciliation,
    outbox_root,
)
from .sentinel import CostSentinel, resolve_daily_budget  # noqa: F401
from .whitelist import (  # noqa: F401
    DailyWhitelist,
    WhitelistAuditor,
    load_whitelist,
    whitelist_path,
)

__all__ = [
    "Alert",
    "AlertOutbox",
    "CostEvent",
    "SwitchEvent",
    "CostSentinel",
    "DailyWhitelist",
    "DeliveryStatus",
    "WhitelistAuditor",
    "classify",
    "decide_escalation",
    "load_cost_events",
    "load_switch_events",
    "load_whitelist",
    "needs_reconciliation",
    "outbox_root",
    "resolve_daily_budget",
    "whitelist_path",
]
