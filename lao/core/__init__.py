"""LAO core — 懂人性层核心引擎（BMC + 意图衰减 + 行为轨迹）"""
from .behavior_tokenizer import (
    BehaviorToken,
    BehaviorTokenSequence,
    ActionType,
    behavior_silence_token,
)
from .behavioral_markov_chain import BehavioralMarkovChain, MultiOrderBMC
from .intention_decay import IntentionDecayModel, IntentionRecord
from .human_nature_engine import HumanNatureEngine, UserState

__all__ = [
    "BehaviorToken",
    "BehaviorTokenSequence",
    "ActionType",
    "behavior_silence_token",
    "BehavioralMarkovChain",
    "MultiOrderBMC",
    "IntentionDecayModel",
    "IntentionRecord",
    "HumanNatureEngine",
    "UserState",
]
