"""
LAO — The Human-Calibration Layer for Your Agents
==================================================

LAO (Long-term Anchored Ontology) 是 LLM 到执行之间的"人性校准层"。

它解决的是 LLM 的两个结构性缺陷：
1. 忘事 — 上下文窗口有限 + KV-Cache 衰减，长对话后记不住用户说过什么
2. 胡说 — 极大似然估计永远选最高概率 token，但最高概率 ≠ 正确

LAO 用行为级概率建模（BMC + 意图衰减 + 行为轨迹）替代 LLM 的压缩式记忆，
让 Agent 像人一样"记得住 + 不胡说"。

3 行代码体验：
    from lao import LAOAgent
    ai = LAOAgent()                              # ① 启动
    ai.watch("member_0421", "下周会来训练")       # ② 记录用户行为/承诺
    ai.predict("member_0421")                    # ③ 预测下一个行为概率

产品定位：聪明是通用的（LLM），LAO 是专用的（人性校准）。
"""

from .core.human_nature_engine import HumanNatureEngine
from .core.intention_decay import IntentionDecayModel, IntentionRecord
from .core.behavior_tokenizer import BehaviorToken, BehaviorTokenSequence, ActionType
from .core.behavioral_markov_chain import BehavioralMarkovChain, MultiOrderBMC
from .evolution.constraint_generator import (
    ConstraintGenerator,
    Constraint,
    ConstraintLevel,
    ConstraintDomain,
)
from .evolution.rule_registry import RuleRegistry

__version__ = "0.1.0"
__all__ = [
    "LAOAgent",
    "HumanNatureEngine",
    "IntentionDecayModel",
    "IntentionRecord",
    "BehaviorToken",
    "BehaviorTokenSequence",
    "ActionType",
    "BehavioralMarkovChain",
    "MultiOrderBMC",
    "ConstraintGenerator",
    "Constraint",
    "ConstraintLevel",
    "ConstraintDomain",
    "RuleRegistry",
]


class LAOAgent:
    """
    LAO 门面类 — 极简"人性校准层"接口
    
    包装底层三个引擎：
    - HumanNatureEngine：BMC + 意图衰减 + 流失风险
    - RuleRegistry：约束注册表（经验复利）
    - ConstraintGenerator：约束生成
    
    设计目标：3 行代码跑通"懂人性"闭环——
        一行启动、一行记录、一行预测。
    
    用法:
        from lao import LAOAgent
        ai = LAOAgent()
        
        # 记录用户行为（watch = 观察）
        ai.watch("member_0421", "用户来店训练了45分钟")
        ai.watch("member_0421", "用户说：下周会来训练")
        
        # 预测该用户下一个行为
        probs = ai.predict("member_0421")
        # → {"action_checkin": 0.45, "action_silence": 0.3, ...}
    """

    def __init__(
        self,
        registry_base_dir: str | None = None,
        sticky: bool = True,
    ):
        """
        Args:
            registry_base_dir: 约束注册表持久化目录（缺省=内存）
            sticky: 是否保留内存内的行为序列（True=跨实例共享，便于demo）
        """
        self._engine = HumanNatureEngine()
        # 约束注册表永远初始化（默认用 lao/evolution/registry 持久化）
        self._registry = RuleRegistry (registry_base_dir) if registry_base_dir else RuleRegistry()
        self._constraint_gen = ConstraintGenerator(rule_registry=self._registry)
        
        # 简单的关键词 → 行为类型推断
        self._action_keywords = {
            ActionType.CHECKIN: ["训练", "到店", "打卡", "练了", "checkin", "来了"],
            ActionType.PURCHASE: ["购买", "续费", "续约", "买", "订单", "purchase"],
            ActionType.DIALOG: ["说", "讲", "问", "告诉", "dialog", "说："],
            ActionType.CANCEL: ["取消", "退费", "退", "不再", "cancel", "停"],
            ActionType.REGISTER: ["注册", "新用户", "加入", "register", "报名"],
            ActionType.SILENCE: ["沉默", "没来", "缺席", "silence", "静默"],
        }

    # ═══════════════════════════════════════════════════════
    # 核心门面 API
    # ═══════════════════════════════════════════════════════

    def watch(self, user_id: str, behavior_text: str, sentiment: float = 0.0) -> str:
        """
        记录用户的一个行为（观察）
        
        从自然语言行为描述自动推断行为类型，存入 BMC 引擎。
        若文本含承诺/意图词，自动同时记录为 active intention。
        
        Args:
            user_id: 用户ID
            behavior_text: 行为描述（自然语言）
            sentiment: 情绪值（-1~1，0=中性）
        
        Returns:
            推断出的行为类型代码
        """
        action_type = self._infer_action_type(behavior_text)
        # 超时字段用默认当前时间
        self._engine.add_behavior(
            user_id,
            BehaviorToken(
                user_id=user_id,
                action_type=action_type,
                metadata={"text": behavior_text, "sentiment": sentiment},
            ),
        )
        # 若行为文本含承诺/意图词 → 同时记录 active intention
        intention_kws = ["会来", "下周", "明天", "续费", "续约", "准备", "打算", "想续", "要坚持", "保证", "加油做"]
        if any(kw in behavior_text for kw in intention_kws):
            self._engine.decay.record_intention(user_id, behavior_text, initial_p=0.5 + sentiment * 0.2)
        return action_type.value

    def record_intention(self, user_id: str, text: str, initial_p: float = 0.7) -> IntentionRecord:
        """记录用户说过的承诺（意图）"""
        return self._engine.decay.record_intention(user_id, text, initial_p)

    def predict(self, user_id: str) -> dict:
        """
        预测该用户的下一个行为概率 + 履约概率 + 建议
        
        返回结构化结果（团队公认的 demo 卖点）：
        {
            "follow_through_prob": 0.32,   # 履约概率（7年门店数据教出的核心）
            "next_action_prob": {...,},     # 下一个行为概率分布
            "suggestion": "...",            # 建议动作
            "active_intentions": [...]        # 活跃意图
        }
        """
        state = self._engine.get_user_state(user_id)
        nxt = state.next_action_prob
        # 履约概率 = 基于用户兑现率 + 下一行为是 checkin 的倾向
        fulfillment = self._engine.decay.get_fulfillment_rate(user_id)
        checkin_bias = nxt.get(ActionType.CHECKIN.value, 0)
        follow_through = round(fulfillment * 0.6 + checkin_bias * 0.4, 2)
        
        # 建议动作（规则驱动·非 LLM）
        suggestion = self._suggest(follow_through, nxt, state)
        
        return {
            "follow_through_prob": follow_through,
            "next_action_prob": nxt or {},
            "suggestion": suggestion,
            "active_intentions": [i["text"] for i in state.active_intentions],
        }

    def _suggest(self, follow_through, nxt, state) -> str:
        """基于履约概率的规则建议"""
        if follow_through < 0.3:
            return "该用户履约率低，建议人工关怀或降低承诺预期"
        if follow_through < 0.7:
            silence_p = nxt.get(ActionType.SILENCE.value, 0)
            if silence_p > 0.3:
                return "该用户有沉默风险，建议设置D+3提醒触达"
            return "该用户履约率中等，建议设置D+3提醒巩固"
        return "该用户履约率高，可放心推进续费/长期计划"

    def watch_and_see(self, user_id: str, behavior_text: str) -> dict:
        """记录行为并立即返回最新预测（一条龙）"""
        self.watch(user_id, behavior_text)
        return self.predict(user_id)

    def state(self, user_id: str) -> dict:
        """
        获取用户完整状态（懂人性的最终产物）
        
        Returns:
            {
                "next_action_prob": {...},
                "active_intentions": [...],
                "trait": {...},
                "churn_risk": 0.47,
                "renewal_prob": 0.23
            }
        """
        return self._engine.get_user_state(user_id).to_dict()

    def is_there_a_twatch_out(self, user_id: str) -> bool:
        """（内部/演示）检查是否有预警"""
        state = self._engine.get_user_state(user_id)
        return state.churn_risk > 0.5

    # ═══════════════════════════════════════════════════════
    # 约束/经验复利
    # ═══════════════════════════════════════════════════════

    def add_constraint(self, description: str, trigger: str, level: str = "yellow") -> str:
        """添加一条经验约束"""
        from .evolution.constraint_generator import ConstraintLevel as CL
        lvl = {"red": CL.RED, "yellow": CL.YELLOW, "green": CL.GREEN}.get(level, CL.YELLOW)
        c = self._constraint_gen.from_behavior_pattern(
            pattern_name="user_added",
            description=description,
            trigger_pattern=trigger,
            level=lvl,
        )
        return c.id

    def check(self, text: str) -> dict:
        """检查文本是否触犯约束（幻觉/违规检测）"""
        hits = []
        if self._registry:
            for c in self._registry.query():
                if c.get("trigger_pattern") and self._match(c["trigger_pattern"], text):
                    hits.append({"id": c["id"], "rule": c.get("rule", "")})
        return {"violated": len(hits) > 0, "hits": hits}

    # ═══════════════════════════════════════════════════════
    # 内部工具
    # ═══════════════════════════════════════════════════════

    def _infer_action_type(self, text: str) -> ActionType:
        """从行为描述推断行为类型"""
        text_lower = text.lower()
        for action, keywords in self._action_keywords.items():
            if any(kw in text_lower for kw in keywords):
                return action
        return ActionType.DIALOG  # 默认对话

    def _match(self, pattern: str, text: str) -> bool:
        import re
        try:
            return bool(re.search(pattern, text, re.IGNORECASE))
        except re.error:
            return pattern.lower() in text.lower()

    def __repr__(self):
        return f"<LAOAgent v{__version__} — 人性校准层>"
