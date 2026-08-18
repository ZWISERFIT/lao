# v3.5.1-fix: A1-A3
# v3.5.1-glm: A1-A3
"""
Cognitive Anchor Framework — LAO 2.7 P0-①
=========================================

L2 从"纯 key-value 事实存储"升级为三层递进认知锚点：

    FactAnchor      (事实)     : "东莞万江" — 确定性事实 key-value（已有 MemoryAnchor）
    DecisionAnchor  (规则)     : "退款>¥500 → 人工介入" — 显式规则/决策逻辑
    CognitiveAnchor (认知模型) : "短期损失优先保护长期信任资产" — 底层认知原则

从"Agent 记规则"到"Agent 理解为什么"的升级（LAO Kernel 架构定稿 2026-08-10）。

anchor_type 统一: "fact" | "decision" | "cognitive"
（对齐 P0-② Experience Contract 的 anchor_type 字段，供 session 注入分层呈现）

三层关系:
    Fact(是什么) → Decision(怎么做) → Cognitive(为什么)
    Cognitive 是最高层，约束 Decision；Decision 依赖 Fact；Fact 是基础。

示例（创始人 7 年经营智慧）:
    FactAnchor:      "退货政策：7天无理由"
    DecisionAnchor:  principle="客户信任优先于短期收入"
                     trigger_condition="投诉涉及退款>¥500"
                     action_rule="人工介入·创始人决策"
                     counter_examples=["低风险投诉可自动处理"]
                     derived_from_events=["2024年3月退款纠纷"]
    CognitiveAnchor: principle="短期损失优先保护长期信任资产"
                     applicability=["客户纠纷","退款","投诉"]
                     conflicts=["追求当期利润最大化"]
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
import hashlib
import json
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# 锚点数据类型
# ---------------------------------------------------------------------------

@dataclass
class Anchor:
    """统一锚点结构（fact / decision / cognitive 三层通用）。"""
    anchor_id: str
    anchor_type: str            # "fact" | "decision" | "cognitive"
    value: Any                  # Fact 为值；Decision/Cognitive 为 dict
    source: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tags: List[str] = field(default_factory=list)
    trust_weight: float = 1.0   # 0.0-1.0, >=0.8 视为 Tier0 永固
    fixture_pair_id: Optional[str] = None   # A1-A3: 关联的回归重放夹具对ID
    # 创始人修正1(2026-08-19): L3 经验分类 — agent_runtime(不需授权·自动同步Momo)
    #                                / user_personal(需授权) / collaborative(需授权)
    experience_type: str = "agent_runtime"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def content_hash(self) -> str:
        """内容哈希，用于完整性验证（存证链）。"""
        payload = {"anchor_id": self.anchor_id, "type": self.anchor_type, "value": self.value}
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

    def run_fixture_replay(self, route_fn: Any) -> Dict[str, Any]:
        """用 self.fixture_pair_id 从 store 查找 FixturePair 并执行回归重放（A3）。

        从 validation.fixture_pair 模块的 fixture_pair_store 中按 fixture_pair_id
        查找对应的 FixturePair，调用 replay_pairs 执行重放并返回统计结果。
        fixture_pair_id 为 None 时跳过；查找或重放失败时返回 skipped 结构。

        Args:
            route_fn: 接受 context 返回 "BLOCK"|"PASS" 的调用函数。

        Returns:
            replay_pairs 的统计 dict，或 {"skipped": True, "reason": ...}。
        """
        if self.fixture_pair_id is None:
            return {"skipped": True, "reason": "no fixture_pair_id"}
        try:
            from lao.effect_anchored.validation.fixture_pair import (
                fixture_pair_store,
                replay_pairs,
            )
            pair = fixture_pair_store.get(self.fixture_pair_id)
            if pair is None:
                return {
                    "skipped": True,
                    "reason": f"fixture pair {self.fixture_pair_id} not found",
                }
            return replay_pairs([pair], self, route_fn)
        except Exception as e:
            return {"skipped": True, "reason": str(e)}


@dataclass
class DecisionAnchor(Anchor):
    """DecisionAnchor: 显式决策规则/逻辑（"为什么这么做"）。"""
    # 结构对齐 Zeus 指令 (2026-08-10):
    #   principle / trigger_condition / action_rule / counter_examples / derived_from_events
    pass  # 通过 value 字典承载上述字段，Anchor 基类保持统一接口


# ---------------------------------------------------------------------------
# 认知锚点存储
# ---------------------------------------------------------------------------

# 缓存空间收敛(2026-08-16 创始人令·L2): 有限空间记住更多更全
# - MAX_ANCHORS: 锚点总数上限, 超限按 (trust_weight, updated) 淘汰最弱最旧
# - Tier0 永固锚点(trust_weight >= 0.8)永不淘汰
# - MAX_HISTORY: 单锚点版本历史封顶, 只保留最近 N 版(旧版裁剪)
MAX_ANCHORS = 500
MAX_HISTORY = 5


class CognitiveAnchorStore:
    """
    三层认知锚点存储（Fact / Decision / Cognitive）。

    提供:
        put(layer, anchor)       — 分层写入锚点（内容哈希 + 版本追溯）
        get(anchor_id)          — 确定性读取（无向量近似）
        lookup(layer, tags)     — 按层/标签检索
        query(trigger)          — 按触发器匹配 Decision/Cognitive（决策查询）
        verify(content_hash)    — 完整性验证

    核心区别（vs 普通 Memory）:
        Memory:   "Suzanne 喜欢快速回复"（数据）
        Anchor:   "客户信任优先于短期收入·高风险投诉人工介入"（Decision Logic）
                  —— 这决定了 "为什么这么做"，不是 "说过什么"。
    """

    def __init__(self, store_path: Optional[str] = None,
                 max_anchors: int = MAX_ANCHORS, max_history: int = MAX_HISTORY):
        self._anchors: Dict[str, Dict[str, Any]] = {}
        self._path = store_path
        self._max_anchors = max(1, int(max_anchors))
        self._max_history = max(1, int(max_history))
        if store_path:
            self._load()

    # -- 写入 -------------------------------------------------------------

    def put(self, anchor: Anchor) -> str:
        """写入锚点，返回 content_hash。同一 anchor_id 重复写入=版本更新(保留history)。"""
        h = anchor.content_hash
        entry = self._anchors.get(anchor.anchor_id)
        if entry is None:
            cur = anchor.to_dict()
            cur.setdefault("version", 1)  # 新锚点版本=1
            self._anchors[anchor.anchor_id] = {
                "current": cur,
                "hash": h,
                "history": [],  # 版本追溯: 旧值列表
                "updated": datetime.now(timezone.utc).isoformat(),
            }
        else:
            # 保留旧版本到 history（不覆盖破坏·封顶 max_history 只留最近N版）
            entry["history"].append(entry["current"])
            entry["history"] = entry["history"][-self._max_history:]
            cur = anchor.to_dict()
            # step2: 自动递增版本号
            prev_v = int((entry["current"].get("version") or 1))
            cur["version"] = prev_v + 1
            entry["current"] = cur
            entry["hash"] = h
            entry["updated"] = datetime.now(timezone.utc).isoformat()
        self._evict_if_needed()
        if self._path:
            self._save()
        return h

    def _evict_if_needed(self) -> None:
        """缓存空间收敛: 超 max_anchors 时淘汰最弱最旧的非 Tier0 锚点。

        淘汰序 = (trust_weight 升序, updated 升序) — 低信任+久未更新的先出局;
        trust_weight >= 0.8(Tier0 永固)与刚写入的锚点不出局。
        """
        if len(self._anchors) <= self._max_anchors:
            return
        def _rank(item):
            anchor_id, entry = item
            cur = entry.get("current", {})
            tw = float(cur.get("trust_weight", 0) or 0)
            if tw >= 0.8:      # Tier0 永固: 排到最后(实际不会被淘汰)
                tw += 100.0
            return (tw, entry.get("updated", ""))
        victims = sorted(self._anchors.items(), key=_rank)
        overflow = len(self._anchors) - self._max_anchors
        for anchor_id, _ in victims[:overflow]:
            del self._anchors[anchor_id]

    def update_trigger_weight(self, anchor_id: str, delta: float) -> Optional[Dict[str, Any]]:
        """step2: 每次修正触发时更新该锚点的 trigger 权重(阻尼收敛)。

        - 更新 value.trigger_weight(若存在) 或 value.trigger_condition 命中度
        - 触发修正(feedback) → 权重微调, 提升该锚点被优先选用概率
        - 返回更新后的 current；锚点不存在返回 None
        """
        entry = self._anchors.get(anchor_id)
        if entry is None:
            return None
        cur = entry["current"]
        value = cur.get("value")
        if isinstance(value, dict):
            tw = float(value.get("trigger_weight", 0))
            value["trigger_weight"] = round(tw + delta, 4)
            value.setdefault("correction_count", 0)
            value["correction_count"] += 1
        cur["updated"] = datetime.now(timezone.utc).isoformat()
        if self._path:
            self._save()
        return cur

    # -- 确定性读取 -------------------------------------------------------

    def get(self, anchor_id: str) -> Optional[Dict[str, Any]]:
        """确定性读取，无向量近似返回。未找到=None（诚实,不猜）."""
        entry = self._anchors.get(anchor_id)
        if entry:
            return entry["current"]
        return None

    def lookup(self, layer: Optional[str] = None, tags: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """按层(anchor_type)和/或标签检索锚点。"""
        result = []
        for anchor_id, entry in self._anchors.items():
            cur = entry["current"]
            if layer and cur.get("anchor_type") != layer:
                continue
            if tags:
                cur_tags = set(cur.get("tags", []))
                if not cur_tags.intersection(set(tags)):
                    continue
            result.append(cur)
        return result

    # -- 决策查询（核心）--------------------------------------------------

    def query(self, trigger: str) -> List[Dict[str, Any]]:
        """
        决策查询: 输入用户/任务情况 → 返回最匹配的 Decision/Cognitive 锚点。

        这实现 LAO Kernel 的核心能力:
        "输入用户情况 → 输出最匹配经验组合"（Experience Matching 的前身, P2-⑤）
        - 匹配 trigger_condition 含触发词 或 principle 含关键概念的 Decision/Cognitive 锚点
        - 按 trust_weight 降序
        """
        matched = []
        for anchor_id, entry in self._anchors.items():
            cur = entry["current"]
            if cur.get("anchor_type") not in ("decision", "cognitive"):
                continue
            value = cur.get("value", {})
            if isinstance(value, dict):
                # Decision: 匹配 trigger_condition / principle / counter_examples 中的关键词
                tc = str(value.get("trigger_condition", ""))
                pr = str(value.get("principle", ""))
                # 提取触发词（数字、金额、动作关键词）与 trigger 做子串互匹配
                def _hitsata(text: str, probe: str) -> bool:
                    """双向关键词/子串匹配: 判断 trigger/principle 文本与用户输入 probe 是否相关。"""
                    import re as _re
                    # 1) 双向整体子串
                    if text in probe or probe in text:
                        return True
                    # 2) 金额档位匹配
                    def _amt(s):
                        return set(_re.findall(r"(?:¥|￥|>|=|<)?(\d+)", s))
                    if _amt(text) & _amt(probe):
                        return True
                    # 3) 实体词: 显式关键词命中(退款/投诉/纠纷/信任/长期/人工 等业务词)
                    def _entities(s):
                        out = set()
                        for w in ["退款","投诉","纠纷","信任","长期","短期","收入","客户","人工","高风险","风险"]:
                            if w in s:
                                out.add(w)
                        return out
                    if _entities(text) & _entities(probe):
                        return True
                    # 4) 2-char 中文字窗双向匹配(避免粘连块)
                    def _bigrams(s):
                        chars = _re.findall(r"[\u4e00-\u9fff]", s)
                        return {"".join(chars[i:i+2]) for i in range(len(chars)-1)}
                    if _bigrams(text) & _bigrams(probe):
                        return True
                    return False
                if _hitsata(f"{tc} {pr}", trigger) or _hitsata(f"{pr}", trigger):
                    matched.append(cur)
            else:
                # Cognitive: 匹配 applicability 标签
                app = str(value.get("applicability", ""))
                if app and any(k.strip() in trigger for k in app.split(",")):
                    matched.append(cur)
        matched.sort(key=lambda a: -a.get("trust_weight", 0))
        return matched

    # -- 完整性验证 -------------------------------------------------------

    def verify(self, anchor_id: str, content_hash: str) -> Optional[bool]:
        """验证锚点内容哈希是否与存储一致。"""
        entry = self._anchors.get(anchor_id)
        if not entry:
            return None
        return entry["hash"] == content_hash

    def stats(self) -> Dict[str, Any]:
        """容量/分层统计(缓存空间收敛可观测)。"""
        by_type: Dict[str, int] = {}
        tier0 = 0
        history_len = 0
        for entry in self._anchors.values():
            cur = entry.get("current", {})
            by_type[cur.get("anchor_type", "?")] = by_type.get(cur.get("anchor_type", "?"), 0) + 1
            if float(cur.get("trust_weight", 0) or 0) >= 0.8:
                tier0 += 1
            history_len += len(entry.get("history", []))
        return {"total": len(self._anchors), "by_type": by_type, "tier0": tier0,
                "history_versions": history_len,
                "max_anchors": self._max_anchors, "max_history": self._max_history}

    # -- 持久化 ------------------------------------------------------------

    def _load(self) -> None:
        import os
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    self._anchors = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._anchors = {}

    def _save(self) -> None:
        import os
        os.makedirs(os.path.dirname(self._path), exist_ok=True) if os.path.dirname(self._path) else None
        with open(self._path, "w") as f:
            json.dump(self._anchors, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 便捷构造器
# ---------------------------------------------------------------------------

def make_decision_anchor(
    anchor_id: str,
    principle: str,
    trigger_condition: str,
    action_rule: str,
    counter_examples: Optional[List[str]] = None,
    derived_from_events: Optional[List[str]] = None,
    source: Optional[str] = None,
    trust_weight: float = 1.0,
    tags: Optional[List[str]] = None,
) -> DecisionAnchor:
    """构造 DecisionAnchor（对齐 Zeus 指令结构）。"""
    return DecisionAnchor(
        anchor_id=anchor_id,
        anchor_type="decision",
        value={
            "principle": principle,
            "trigger_condition": trigger_condition,
            "action_rule": action_rule,
            "counter_examples": counter_examples or [],
            "derived_from_events": derived_from_events or [],
        },
        source=source,
        trust_weight=trust_weight,
        tags=tags or ["decision"],
    )


def make_cognitive_anchor(
    anchor_id: str,
    principle: str,
    applicability: List[str],
    conflicts: Optional[List[str]] = None,
    source: Optional[str] = None,
    trust_weight: float = 1.0,
    tags: Optional[List[str]] = None,
) -> Anchor:
    """构造 CognitiveAnchor（认知模型·底层原则）。"""
    return Anchor(
        anchor_id=anchor_id,
        anchor_type="cognitive",
        value={
            "principle": principle,
            "applicability": applicability,
            "conflicts": conflicts or [],
        },
        source=source,
        trust_weight=trust_weight,
        tags=tags or ["cognitive"],
    )


def make_fact_anchor(
    anchor_id: str,
    value: Any,
    source: Optional[str] = None,
    trust_weight: float = 1.0,
    tags: Optional[List[str]] = None,
) -> Anchor:
    """构造 FactAnchor（事实·等价 MemoryAnchor, 但带 anchor_type）。"""
    return Anchor(
        anchor_id=anchor_id,
        anchor_type="fact",
        value=value,
        source=source,
        trust_weight=trust_weight,
        tags=tags or ["fact"],
    )
