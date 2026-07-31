"""
意图衰减模型 — LAO v2 懂人性层独立组件
========================================

创始人说："会员说下周会来，但系统不能把这句话当真的执行信令"
LAO知道：他说了→我记住了→概率每天衰减→第7天提醒→第14天标记为无效

衰减函数: P_day = P_initial × e^(-λ × day)

λ(衰减率)从用户历史行为训练：
- 说到做到的人 → λ小（慢衰减）
- 说做不到的人 → λ大（快衰减）

数学本质：
LLM的KV-Cache记忆会衰减（上下文窗口满了就忘了）
LAO意图衰减是有结构衰减（我知道为什么衰减、衰减到多少、什么时候标记无效）
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import math
import json


@dataclass
class IntentionRecord:
    """
    用户意图/承诺记录
    
    一个人的一句话，在这里被结构化为：
    - 说了什么
    - 当时多认真（initial_p）
    - 衰减速度（lambda — 由历史兑现率决定）
    - 什么时候说的（created_at — 用于计算衰减天数）
    """
    text: str
    initial_p: float = 0.7
    lambda_rate: float = 0.1
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: Optional[str] = None
    user_id: Optional[str] = None
    id: str = field(default_factory=lambda: f"int_{int(datetime.now(timezone.utc).timestamp())}")
    
    def __post_init__(self):
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
    
    @property
    def days_elapsed(self) -> float:
        """距离承诺被说出的天数"""
        now = datetime.now(timezone.utc)
        return (now - self.created_at).total_seconds() / 86400.0
    
    def current_probability(self) -> float:
        """
        当前概率 = 初始概率 × e^(-λ × 天数)
        
        第0天: P = P₀ (刚说完，最高)
        第7天: P = P₀ × e^(-7λ) (一周后，衰减)
        第14天: P = P₀ × e^(-14λ) (两周后，接近0)
        """
        days = self.days_elapsed
        return self.initial_p * math.exp(-self.lambda_rate * max(0, days))
    
    def is_expired(self, threshold: float = 0.01) -> bool:
        """意图是否已过期（概率低于阈值）"""
        return self.current_probability() < threshold
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "initial_p": self.initial_p,
            "lambda_rate": self.lambda_rate,
            "created_at": self.created_at.isoformat() if isinstance(self.created_at, datetime) else self.created_at,
            "source": self.source,
            "user_id": self.user_id,
            "days_elapsed": round(self.days_elapsed, 1),
            "current_probability": round(self.current_probability(), 3),
            "expired": self.is_expired(),
        }


class IntentionDecayModel:
    """
    意图衰减引擎
    
    管理用户"说过的话"——即使过了30天，系统还知道这个人曾经说过什么
    
    三个核心能力：
    1. record_intention() — 记录用户说过的承诺
    2. get_active() — 获取当前仍有效的承诺（概率>阈值）
    3. update_fulfillment() — 承诺兑现/失信 → 影响λ
    
    对称于LLM：LLM一句话30轮后忘了 / LAO一句话30天后还知道（但概率衰减了）
    """
    
    def __init__(self):
        # 用户意图集合: user_id → [IntentionRecord, ...]
        self.intentions: Dict[str, List[IntentionRecord]] = {}
        
        # 用户兑现率缓存: user_id → float (0~1)
        # 0.0 = 说了从不做, 1.0 = 说到做到
        self.fulfillment_rates: Dict[str, float] = {}
        
        # 全局默认参数
        self.default_lambda = 0.1       # λ默认值
        self.default_initial_p = 0.7    # 初始概率默认值
        self.expiration_threshold = 0.01 # 过期阈值
    
    def record_intention(
        self,
        user_id: str,
        text: str,
        initial_p: Optional[float] = None,
        source: Optional[str] = None,
    ) -> IntentionRecord:
        """
        记录用户的一个意图/承诺
        
        Args:
            user_id: 用户ID
            text: 承诺原文
            initial_p: 初始概率（缺省=0.7·大多数人说的时候是认真的）
            source: 来源（如"dialog·2026-07-30"）
        
        Returns:
            创建的IntentionRecord
        """
        if initial_p is None:
            initial_p = self.default_initial_p
        
        rate = self._compute_lambda(user_id)
        
        record = IntentionRecord(
            text=text,
            initial_p=initial_p,
            lambda_rate=rate,
            created_at=datetime.now(timezone.utc),
            source=source or "human_nature_engine",
            user_id=user_id,
        )
        
        self.intentions.setdefault(user_id, []).append(record)
        return record
    
    def record_batch(self, user_id: str, texts: List[str]) -> List[IntentionRecord]:
        """批量记录多个意图"""
        return [self.record_intention(user_id, text) for text in texts]
    
    def get_active(
        self,
        user_id: str,
        min_p: float = 0.05,
        max_count: int = 10,
        include_expired: bool = False,
    ) -> List[IntentionRecord]:
        """
        获取用户当前活跃的意图（概率>阈值）
        
        Args:
            user_id: 用户ID
            min_p: 最低概率阈值（默认5%）
            max_count: 最大返回数量
            include_expired: 是否包含已过期的
        
        Returns:
            按当前概率降序排列的意图列表
        """
        records = self.intentions.get(user_id, [])
        
        if not include_expired:
            records = [r for r in records if not r.is_expired(self.expiration_threshold)]
        
        records = [r for r in records if r.current_probability() >= min_p]
        records.sort(key=lambda r: r.current_probability(), reverse=True)
        
        return records[:max_count]
    
    def get_theme_intentions(
        self,
        user_id: str,
        theme_keywords: List[str],
        min_p: float = 0.01,
    ) -> List[IntentionRecord]:
        """按关键词筛选意图（如找所有"续费"相关的承诺）"""
        all_active = self.get_active(user_id, min_p=min_p, max_count=100, include_expired=True)
        return [
            r for r in all_active
            if any(kw in r.text for kw in theme_keywords)
        ]
    
    def update_fulfillment(self, user_id: str, did_fulfill: bool):
        """
        更新用户的兑现率
        
        当用户"说过××然后做了" → did_fulfill=True → 兑现率上升
        当用户"说过××但没做" → did_fulfill=False → 兑现率下降
        
        用指数移动平均(EMA)平滑更新：
        new_rate = old_rate × (1-α) + (1 if did else 0) × α
        α=0.1 表示逐步学习，不是一次就变
        """
        old_rate = self.fulfillment_rates.get(user_id, 0.5)
        alpha = 0.1
        new_rate = old_rate * (1 - alpha) + (1.0 if did_fulfill else 0.0) * alpha
        self.fulfillment_rates[user_id] = round(new_rate, 3)
        
        # 兑现率变化 → 该用户的所有意图λ需要同步更新
        self._sync_lambdas(user_id)
    
    def get_fulfillment_rate(self, user_id: str) -> float:
        """获取用户历史兑现率"""
        return self.fulfillment_rates.get(user_id, 0.5)
    
    def _compute_lambda(self, user_id: str) -> float:
        """
        根据用户兑现率计算衰减率
        
        兑现率=1.0（说到做到） → λ=0.05（极慢衰减·承诺可靠）
        兑现率=0.5（普普通通） → λ=0.125（适中衰减）
        兑现率=0.0（从不兑现） → λ=0.20（快速衰减·承诺不可信）
        
        公式: λ = 0.20 - 0.15 × 兑现率
        """
        rate = self.get_fulfillment_rate(user_id)
        return round(max(0.05, 0.20 - 0.15 * rate), 3)
    
    def _sync_lambdas(self, user_id: str):
        """同步该用户所有意图的λ"""
        new_lambda = self._compute_lambda(user_id)
        for record in self.intentions.get(user_id, []):
            record.lambda_rate = new_lambda
    
    def get_summary(self, user_id: str) -> Dict[str, Any]:
        """获取用户意图的概要统计"""
        all_records = self.intentions.get(user_id, [])
        active = self.get_active(user_id)
        
        return {
            "user_id": user_id,
            "total_intentions": len(all_records),
            "active_count": len(active),
            "expired_count": sum(1 for r in all_records if r.is_expired(self.expiration_threshold)),
            "fulfillment_rate": self.get_fulfillment_rate(user_id),
            "current_lambda": self._compute_lambda(user_id),
            "top_intentions": [r.to_dict() for r in active[:3]],
        }
    
    def clear_user(self, user_id: str):
        """清空用户的所有意图数据"""
        self.intentions.pop(user_id, None)
        self.fulfillment_rates.pop(user_id, None)
    
    def save(self, path: str):
        """保存到文件"""
        data = {
            "intentions": {
                uid: [r.to_dict() for r in records]
                for uid, records in self.intentions.items()
            },
            "fulfillment_rates": self.fulfillment_rates,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    @classmethod
    def load(cls, path: str) -> "IntentionDecayModel":
        """从文件加载"""
        with open(path) as f:
            data = json.load(f)
        
        model = cls()
        for uid, records_data in data.get("intentions", {}).items():
            for rd in records_data:
                model.intentions.setdefault(uid, []).append(
                    IntentionRecord(
                        text=rd["text"],
                        initial_p=rd.get("initial_p", 0.7),
                        lambda_rate=rd.get("lambda_rate", 0.1),
                        created_at=rd.get("created_at", datetime.now(timezone.utc).isoformat()),
                        source=rd.get("source"),
                        user_id=uid,
                        id=rd.get("id", f"int_{int(datetime.now(timezone.utc).timestamp())}"),
                    )
                )
        model.fulfillment_rates = data.get("fulfillment_rates", {})
        return model
