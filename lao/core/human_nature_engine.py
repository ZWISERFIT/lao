"""
HumanNatureEngine — LAO v2 懂人性层顶层接口
============================================

创始人说：懂人性 → 自然过渡到懂业务

这个引擎的职责：
1. 接收用户的完整行为历史
2. 用BMC引擎预测下一个行为概率
3. 用意图衰减模型管理用户的"说过的话"
4. 输出：用户当前真实状态的精确概率分布

完全不需要LLM参与——纯概率+规则。
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
import math
import time

from .behavior_tokenizer import ActionType, BehaviorToken, BehaviorTokenSequence
from .behavioral_markov_chain import BehavioralMarkovChain, MultiOrderBMC


@dataclass
class IntentionRecord:
    """
    用户意图/承诺记录
    
    创始人说："会员说下周会来，但系统不能把这句话当真的执行信令"
    LAO知道：他说了→我记住了→概率每天衰减→第7天提醒→第14天标记为无效
    
    衰减函数: P_day = P_initial × e^(-λ × day)
    """
    text: str
    initial_p: float = 0.7
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source_action_type: Optional[str] = None
    lambda_rate: float = 0.1  # 默认衰减率
    
    @property
    def days_elapsed(self) -> float:
        now = datetime.now(timezone.utc)
        if isinstance(self.created_at, str):
            from datetime import datetime as dt_mod
            self.created_at = dt_mod.fromisoformat(self.created_at)
        return (now - self.created_at).total_seconds() / 86400.0
    
    def current_probability(self) -> float:
        days = self.days_elapsed
        return self.initial_p * math.exp(-self.lambda_rate * days)


class IntentionDecayModel:
    """
    意图衰减模型
    
    管理用户曾经说过的话/做过的承诺：
    1. 记录用户意图（来自dialog行为）
    2. 用衰减函数管理概率随时间递减
    3. λ（衰减率）由用户历史兑现率训练
    """
    
    def __init__(self):
        self.intentions: Dict[str, List[IntentionRecord]] = {}
        # 用户历史兑现率缓存: user_id → ratio
        self.fulfillment_rates: Dict[str, float] = {}
    
    def record_intention(
        self, 
        user_id: str, 
        text: str, 
        initial_p: float = 0.7,
        source_action_type: Optional[str] = None,
    ) -> IntentionRecord:
        """
        记录用户的一个意图/承诺
        
        初始概率(70%)：表示用户说这句话时基本是认真的
        λ由用户历史兑现率决定：
        - 此人说了10次"下周来"只来了3次 → λ = 0.2(快速衰减)
        - 此人说到做到 → λ = 0.05(缓慢衰减)
        """
        rate = self._compute_lambda(user_id)
        
        record = IntentionRecord(
            text=text,
            initial_p=initial_p,
            created_at=datetime.now(timezone.utc),
            source_action_type=source_action_type,
            lambda_rate=rate,
        )
        
        self.intentions.setdefault(user_id, []).append(record)
        return record
    
    def get_active_intentions(
        self, 
        user_id: str, 
        min_p: float = 0.01,
        max_count: int = 10,
    ) -> List[IntentionRecord]:
        """获取用户现有意图中概率>阈值的"""
        records = self.intentions.get(user_id, [])
        active = [r for r in records if r.current_probability() >= min_p]
        active.sort(key=lambda r: r.current_probability(), reverse=True)
        return active[:max_count]
    
    def get_fulfillment_rate(self, user_id: str) -> float:
        """获取用户的兑现率"""
        return self.fulfillment_rates.get(user_id, 0.5)
    
    def update_fulfillment(self, user_id: str, said: str, did: bool):
        """
        更新用户的兑现率
        
        当用户"说过××然后做了" → 标记兑现成功
        当用户"说过××但没做" → 标记兑现失败
        迭代更新兑现率
        """
        rate = self.fulfillment_rates.get(user_id, 0.5)
        # 简单的指数移动平均
        alpha = 0.1
        new_rate = rate * (1 - alpha) + (1.0 if did else 0.0) * alpha
        self.fulfillment_rates[user_id] = new_rate
    
    def _compute_lambda(self, user_id: str) -> float:
        """
        根据用户兑现率计算衰减率
        
        兑现率高(>0.7) → 慢衰减(λ=0.05)
        兑现率低(<0.3) → 快衰减(λ=0.2)
        """
        rate = self.get_fulfillment_rate(user_id)
        # λ = 0.2 - 0.15 × 兑现率
        # 兑现率0.5 → λ=0.125, 兑现率0.8 → λ=0.08, 兑现率0.2 → λ=0.17
        return max(0.05, 0.2 - 0.15 * rate)


@dataclass
class UserState:
    """
    用户当前状态的完整快照
    
    这是"懂人性"的最终产物——
    BMC预测 + 意图衰减 + 用户特质 → 统一出口
    """
    user_id: str
    next_action_prob: Dict[str, float]
    active_intentions: List[Dict[str, Any]]
    trait: Dict[str, Any]
    churn_risk: float
    renewal_prob: float
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "next_action_prob": self.next_action_prob,
            "active_intentions": [
                {
                    "text": i["text"],
                    "probability": i["probability"],
                    "days_elapsed": i.get("days_elapsed", 0),
                }
                for i in self.active_intentions
            ],
            "trait": self.trait,
            "churn_risk": self.churn_risk,
            "renewal_prob": self.renewal_prob,
            "updated_at": self.updated_at,
        }


class HumanNatureEngine:
    """
    懂人性层顶层引擎
    
    数据流：
    1. 行为token流 → BMC引擎 → 预测下一个行为概率
    2. dialog行为 → 意图衰减模型 → 管理用户承诺
    3. 用户特质 → 从历史行为中提取
    4. 输出：UserState（结构化概率数据，零幻觉）
    """
    
    def __init__(self):
        self.bmc = MultiOrderBMC()
        self.decay = IntentionDecayModel()
        self.trait_profiles: Dict[str, Dict[str, Any]] = {}
        self.sequences: Dict[str, BehaviorTokenSequence] = {}
    
    def add_behavior(self, user_id: str, behavior: BehaviorToken) -> None:
        """
        添加一个用户行为 → 所有组件自动更新
        
        - BMC引擎更新转移矩阵
        - dialog类的行为 → 提取意图到衰减模型
        - 用户特质增量更新
        """
        # 序列管理
        if user_id not in self.sequences:
            self.sequences[user_id] = BehaviorTokenSequence(user_id)
        self.sequences[user_id].add(behavior)
        
        # BMC引擎
        self.bmc.add_behavior(user_id, behavior)
        
        # 如果是dialog行为，提取意图
        if behavior.action_type == ActionType.DIALOG:
            text = behavior.metadata.get("text") or behavior.metadata.get("message", "")
            sentiment = behavior.metadata.get("sentiment", 0)
            if text:
                # 提取可能包含"承诺"的对话
                intention_keywords = [
                    "会来", "下周", "明天", "一定", "肯定", "保证",
                    "续费", "续约", "坚持", "会去",
                ]
                if any(kw in text for kw in intention_keywords):
                    self.decay.record_intention(
                        user_id=user_id,
                        text=text,
                        initial_p=0.5 + sentiment * 0.2,  # 情绪正面→概率高
                        source_action_type=ActionType.DIALOG.value,
                    )
        
        # 更新用户特质
        self._update_trait(user_id, behavior)
    
    def get_user_state(self, user_id: str) -> UserState:
        """
        返回用户当前真实状态的概率分布
        
        这是"懂人性"的最终输出产物——
        完全不需要LLM，纯概率+规则
        """
        seq = self.sequences.get(user_id)
        user_traits = self.trait_profiles.get(user_id, {})
        
        # 1. BMC预测下一个行为
        next_action = self.bmc.predict(user_id)
        
        # 2. 意图衰减 - 获取有效意图
        intentions = self.decay.get_active_intentions(user_id)
        intention_data = [
            {
                "text": i.text,
                "probability": i.current_probability(),
                "days_elapsed": round(i.days_elapsed, 1),
            }
            for i in intentions
        ]
        
        # 3. 流失风险计算（纯概率+规则）
        churn_risk = self._compute_churn_risk(next_action, user_traits, seq)
        
        # 4. 续费概率
        renewal_prob = self._compute_renewal_prob(next_action, user_traits)
        
        return UserState(
            user_id=user_id,
            next_action_prob=next_action,
            active_intentions=intention_data,
            trait=user_traits,
            churn_risk=churn_risk,
            renewal_prob=renewal_prob,
        )
    
    def _update_trait(self, user_id: str, behavior: BehaviorToken):
        """增量更新用户特质"""
        traits = self.trait_profiles.setdefault(user_id, {})
        
        # 行为计数
        action_type = behavior.action_type.value
        counts = traits.setdefault("action_counts", {})
        counts[action_type] = counts.get(action_type, 0) + 1
        
        # 总天数
        seq = self.sequences.get(user_id, BehaviorTokenSequence(user_id))
        if len(seq) >= 2:
            first = seq[0]
            last = seq[-1]
            if hasattr(first.timestamp, "isoformat"):
                days_active = (last.timestamp - first.timestamp).total_seconds() / 86400.0
                traits["days_active"] = round(days_active, 1)
        
        # 签到率（checkin占所有行为的比例）
        total = sum(counts.values())
        checkins = counts.get(ActionType.CHECKIN.value, 0)
        traits["checkin_rate"] = round(checkins / total, 3) if total > 0 else 0
        
        # 沉默阈值：此人通常沉默几天后开始活跃
        # 从序列中提取沉默→checkin的转换模式
        traits["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    def _compute_churn_risk(
        self, 
        next_action: Dict[str, float], 
        traits: Dict[str, Any],
        seq: Optional[BehaviorTokenSequence],
    ) -> float:
        """
        流失风险评分
        
        因素：
        - 如果预测的高概率行为是silence → 高风险
        - 如果承诺衰减到0 → 高风险
        - 如果上次是cancel → 极高风险
        - checkin_rate低 → 风险高
        """
        risk = 0.0
        
        # 如果silence是最高概率行为
        silence_p = next_action.get(ActionType.SILENCE.value, 0)
        cancel_p = next_action.get(ActionType.CANCEL.value, 0)
        
        risk += silence_p * 0.6
        risk += cancel_p * 0.8
        risk += (1 - traits.get("checkin_rate", 0.5)) * 0.3
        
        # 检查最后一个行为是否是cancel
        if seq and seq.last():
            if seq.last().action_type == ActionType.CANCEL:
                risk += 0.5
        
        return min(1.0, max(0.0, risk))
    
    def _compute_renewal_prob(
        self, 
        next_action: Dict[str, float], 
        traits: Dict[str, Any],
    ) -> float:
        """续费概率——基于行为轨迹推断"""
        purchase_p = next_action.get(ActionType.PURCHASE.value, 0)
        checkin_p = next_action.get(ActionType.CHECKIN.value, 0)
        
        # 活跃用户 + 有purchase历史 = 续费概率高
        prob = purchase_p * 0.5 + checkin_p * 0.3
        prob += traits.get("checkin_rate", 0.5) * 0.2
        
        return min(1.0, max(0.0, prob))
