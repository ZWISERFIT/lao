# v3.5.2-laorefactor: R4
"""
RISBridgeConsumer — R4.1 RIS→LAO 数据飞轮消费者 (PRD v1.1·2026-08-19 双失联根治)
=================================================================================

ris-bridge.json 此前"只写不读"(RIS 侧 sync 单向落盘·LAO 侧无消费)。
本模块补上消费端·闭合创始人 22:51 令的数据飞轮:

    ① provider_unavailable / provider_isolation 事件 → LAO 路由 provider 黑名单
       (隔离键·持久化到 data/provider-blacklist.json·provider_ok 事件解除)
    ② recovery 恢复经验( *_recovery / status=recovered ) → ExperienceExtractor
       灌入 LAO 经验库(lao_experiences.jsonl·ExperienceExtractor 格式)

铁律(PRD 第4节质量红线):
    - 禁止跨包 import: 只读共享 JSON·不 import ris 包任何模块
    - 本模块头部纯标准库(ExperienceExtractor 延迟导入·同为 lao 包内)
    - fail-open: 任何异常不抛出·返回统计(桥文件损坏不阻断 LAO)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DEFAULT_BRIDGE_FILE = os.environ.get(
    "RIS_BRIDGE_FILE", os.path.expanduser("~/shared/state/ris-bridge.json")
)

# 黑名单持久化(隔离键·供 LAO 路由/审计消费)
_BLACKLIST_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "provider-blacklist.json")

# 事件类型语义(PRD R4.1: provider_unavailable/isolation → 黑名单)
_BLACKLIST_EVENT_TYPES = ("provider_unavailable", "provider_isolation")
_RELEASE_EVENT_TYPES = ("provider_ok",)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_provider(event: Dict[str, Any]) -> str:
    """从事件提取 provider 名(agent_id 优先·detail.provider 兜底)。"""
    detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
    return str(event.get("agent_id") or detail.get("provider") or "").strip()


def _iter_events(bridge: Dict[str, Any]) -> List[Dict[str, Any]]:
    """合并 recent_events + active_alerts(按 ts 去重·时间升序)。"""
    seen, events = set(), []
    for e in (bridge.get("recent_events") or []) + (bridge.get("active_alerts") or []):
        if not isinstance(e, dict):
            continue
        key = (str(e.get("ts", "")), str(e.get("event_type", "")),
               str(e.get("agent_id", "")))
        if key in seen:
            continue
        seen.add(key)
        events.append(e)
    events.sort(key=lambda e: str(e.get("ts", "")))
    return events


def _is_recovery(event: Dict[str, Any]) -> bool:
    """恢复类事件(RIS 五步闭环 Record 产物·与 ris/bridge.py 口径一致)。"""
    et = str(event.get("event_type", ""))
    return et.endswith("_recovery") or event.get("status") in ("recovered", "recovering")


def _recovery_record(event: Dict[str, Any]) -> Dict[str, Any]:
    """恢复事件 → ExperienceExtractor 输入格式(规格 2.1)。"""
    et = str(event.get("event_type", "unknown"))
    agent_id = str(event.get("agent_id") or "ris")
    detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
    verified = bool(event.get("verified", True)) and \
        event.get("status") in ("recovered", "recovering", None, "")
    return {
        "request_id": f"ris-{et}-{event.get('ts', '')}",
        "request_features": {
            "task_text": f"RIS恢复经验: {et} (agent={agent_id}, ts={event.get('ts', '')})",
            "task_type": "fact",
            "agent_id": agent_id,
            "model_used": str(detail.get("model", "") or ""),
            "provider_used": str(detail.get("provider", "") or ""),
            "context_tokens": 0,
        },
        "response_features": {
            "response_text": f"{et} 恢复动作已执行: {json.dumps(detail, ensure_ascii=False)[:200]}",
            "response_tokens": 0,
            "cache_hit": False,
            "actual_cost": 0.0,
        },
        "verification": {
            "fact_verified": verified,
            "cognitive_consistent": True,
            "retry_count": int(detail.get("attempts", 0) or 0),
        },
    }


def _load_blacklist(path: str) -> Dict[str, Any]:
    """读现有黑名单(损坏/缺失 → 空·fail-open)。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_blacklist(path: str, blacklist: Dict[str, Any]) -> bool:
    """原子写黑名单(tmp + replace·防并发读到半写文件)。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(blacklist, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def consume_bridge(bridge_file: str = DEFAULT_BRIDGE_FILE,
                   store_path: Optional[str] = None,
                   blacklist_path: Optional[str] = None,
                   extractor: Optional[Any] = None) -> Dict[str, Any]:
    """消费 ris-bridge.json → provider 黑名单 + 恢复经验灌入经验库。

    Args:
        bridge_file: 桥 JSON 路径(默认共享态 ris-bridge.json)。
        store_path: 经验库 JSONL 路径(None=ExperienceExtractor 默认)。
        blacklist_path: 黑名单持久化路径(None=lao data/provider-blacklist.json)。
        extractor: 可注入 ExperienceExtractor(测试用·None=延迟导入默认)。

    Returns:
        消费统计 Dict:
          ok / events_consumed / recovery_events / experiences_ingested /
          providers_blacklisted(本次新增) / providers_released(本次解除) /
          blacklist_active(当前生效) / blacklist_file
    """
    result: Dict[str, Any] = {
        "ok": False, "events_consumed": 0, "recovery_events": 0,
        "experiences_ingested": 0, "providers_blacklisted": [],
        "providers_released": [], "blacklist_active": [],
        "blacklist_file": blacklist_path or _BLACKLIST_PATH,
    }
    try:
        with open(bridge_file, "r", encoding="utf-8") as f:
            bridge = json.load(f)
        if not isinstance(bridge, dict):
            return result
    except Exception:
        return result  # 桥缺失/损坏 → fail-open·不阻断 LAO

    events = _iter_events(bridge)
    result["events_consumed"] = len(events)

    # ── ① provider 黑名单(隔离键): 事件流 + summary.isolated_providers ──
    summary = bridge.get("summary") if isinstance(bridge.get("summary"), dict) else {}
    isolated = {str(p) for p in (summary.get("isolated_providers") or []) if p}
    event_counts: Dict[str, int] = {}
    for e in events:
        provider = _event_provider(e)
        if not provider:
            continue
        et = str(e.get("event_type", ""))
        if et in _BLACKLIST_EVENT_TYPES:
            isolated.add(provider)
            event_counts[provider] = event_counts.get(provider, 0) + 1
        elif et in _RELEASE_EVENT_TYPES:
            isolated.discard(provider)

    bl_path = blacklist_path or _BLACKLIST_PATH
    existing = _load_blacklist(bl_path)
    prev_active = {p for p, v in existing.items()
                   if isinstance(v, dict) and v.get("isolated")}
    merged: Dict[str, Any] = dict(existing)
    now = _now_iso()
    for provider in sorted(set(isolated) | prev_active):
        if provider in isolated:
            entry = dict(merged.get(provider) or {})
            entry.update({
                "isolated": True,
                "reason": "ris_isolation" if provider in event_counts
                          else str((merged.get(provider) or {}).get("reason", "ris_isolation")),
                "events": int(event_counts.get(provider,
                             (merged.get(provider) or {}).get("events", 0)) or 0),
                "source": "ris-bridge",
            })
            entry.setdefault("since", now)
            merged[provider] = entry
        else:
            entry = dict(merged.get(provider) or {})
            entry.update({"isolated": False, "released_at": now, "source": "ris-bridge"})
            merged[provider] = entry
    result["blacklist_active"] = sorted(isolated)
    result["providers_blacklisted"] = sorted(isolated - prev_active)
    result["providers_released"] = sorted(prev_active - isolated)
    _save_blacklist(bl_path, merged)

    # ── ② 恢复经验 → ExperienceExtractor 灌入经验库 ──
    recovery_events = [e for e in events if _is_recovery(e)]
    result["recovery_events"] = len(recovery_events)
    if recovery_events:
        try:
            if extractor is None:
                from lao.effect_anchored.experience_extractor import ExperienceExtractor
                extractor = ExperienceExtractor(store_path=store_path) \
                    if store_path else ExperienceExtractor()
            for e in recovery_events:
                experience_id = extractor.extract(_recovery_record(e))
                if experience_id:
                    result["experiences_ingested"] += 1
        except Exception:
            pass  # 经验灌入失败不阻断(黑名单已生效)

    result["ok"] = True
    return result


__all__ = ["consume_bridge", "DEFAULT_BRIDGE_FILE"]
