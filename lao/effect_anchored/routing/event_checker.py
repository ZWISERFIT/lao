"""EventChecker — L1 独立第三方检查员 (LAO v3.5)

定位：LAO 帮 Runtime 检查好事件再交卷。
在路由决策前，按目标模型的参数白名单检查请求事件：
  - PASS           → 参数齐全且合法，正常路由
  - MISSING_PARAMS → 缺少必需参数，明确告知缺什么（而非笼统报错）
  - REJECT         → 含白名单外的参数/结构非法，直接拒绝，不给 LLM 浪费 Token

每个模型家族（DeepSeek / Qwen / GLM）定义独立的
required_params / optional_params 白名单。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class CheckStatus(str, Enum):
    """事件检查结论。"""

    PASS = "PASS"
    MISSING_PARAMS = "MISSING_PARAMS"
    REJECT = "REJECT"


# ── 参数白名单：每个模型家族的参数要求各不相同 ──────────────────────────────

MODEL_PARAM_WHITELIST: Dict[str, Dict[str, List[str]]] = {
    # DeepSeek 系列(api.deepseek.com): OpenAI 兼容子集
    "deepseek": {
        "required_params": ["model", "messages"],
        "optional_params": [
            "temperature", "max_tokens", "stream", "top_p",
            "frequency_penalty", "presence_penalty", "stop",
            "tools", "tool_choice", "response_format", "seed",
        ],
    },
    # Qwen 系列(token-plan/百炼 DashScope 兼容模式)
    "qwen": {
        "required_params": ["model", "messages"],
        "optional_params": [
            "temperature", "top_p", "max_tokens", "stream", "stop",
            "tools", "tool_choice", "response_format", "seed",
            "enable_thinking", "vl_high_resolution_images",
            "result_format", "incremental_output",
        ],
    },
    # GLM 系列(token-plan/novarouteai)
    "glm": {
        "required_params": ["model", "messages"],
        "optional_params": [
            "temperature", "top_p", "max_tokens", "stream", "stop",
            "tools", "tool_choice", "response_format",
            "thinking", "do_sample", "user_id",
        ],
    },
}

DEFAULT_FAMILY = "deepseek"


def family_for_model(model_name: str) -> str:
    """从模型名推断家族(deepseek/qwen/glm)，未知回落默认家族。"""
    name = (model_name or "").lower()
    if "qwen" in name:
        return "qwen"
    if "glm" in name:
        return "glm"
    if "deepseek" in name:
        return "deepseek"
    return DEFAULT_FAMILY


def resolve_family(model_spec: Union[str, Dict[str, Any], None]) -> str:
    """解析 model_spec → 模型家族。

    支持三种形态：
      - str            : 模型名(如 "glm-5.2")或家族名(如 "glm")
      - dict           : 含 "model" 或 "family" 键的模型描述
      - None           : 回落默认家族
    """
    if isinstance(model_spec, str):
        if model_spec.lower() in MODEL_PARAM_WHITELIST:
            return model_spec.lower()
        return family_for_model(model_spec)
    if isinstance(model_spec, dict):
        model = model_spec.get("model") or model_spec.get("family") or ""
        return resolve_family(str(model))
    return DEFAULT_FAMILY


@dataclass
class CheckResult:
    """事件检查结果。"""

    status: CheckStatus
    missing_params: List[str] = field(default_factory=list)
    model_name: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    unknown_params: List[str] = field(default_factory=list)
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status is CheckStatus.PASS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "missing_params": self.missing_params,
            "model_name": self.model_name,
            "timestamp": self.timestamp,
            "unknown_params": self.unknown_params,
            "reason": self.reason,
        }


class EventChecker:
    """L1 事件检查员：路由前按模型参数白名单检查请求事件。"""

    def __init__(self, whitelist: Optional[Dict[str, Dict[str, List[str]]]] = None):
        """Args:
            whitelist: 可选的自定义参数白量表(结构同 MODEL_PARAM_WHITELIST)。
        """
        self.whitelist = whitelist or MODEL_PARAM_WHITELIST

    def check_event(
        self,
        event: Union[Dict[str, Any], Any],
        model_spec: Union[str, Dict[str, Any], None] = None,
    ) -> CheckResult:
        """检查事件是否满足目标模型的参数要求。

        Args:
            event: Runtime 请求事件( dict，如 {"model": ..., "messages": [...]} )。
            model_spec: 模型名 / 模型描述 dict / 家族名。

        Returns:
            CheckResult:
              - PASS: 必需参数齐全，无白名单外参数
              - MISSING_PARAMS: 缺少必需参数(missing_params 列明)
              - REJECT: 结构非法或含白名单外参数(unknown_params 列明)
        """
        model_name = self._model_name(model_spec)
        ts = datetime.now(timezone.utc).isoformat()

        if not isinstance(event, dict):
            return CheckResult(
                status=CheckStatus.REJECT,
                model_name=model_name,
                timestamp=ts,
                reason=f"事件结构非法: 期望 dict, 实际 {type(event).__name__}",
            )

        family = resolve_family(model_spec)
        spec = self.whitelist.get(family, self.whitelist[DEFAULT_FAMILY])
        required = spec["required_params"]
        optional = spec.get("optional_params", [])
        allowed = set(required) | set(optional)

        keys = set(event.keys())
        unknown = sorted(keys - allowed)
        if unknown:
            return CheckResult(
                status=CheckStatus.REJECT,
                model_name=model_name,
                timestamp=ts,
                unknown_params=unknown,
                reason=(
                    f"含模型 {model_name or family} 白名单外的参数: "
                    f"{', '.join(unknown)} (允许: {', '.join(sorted(allowed))})"
                ),
            )

        # 值为 None / 空容器视同缺失(Runtime 未真正提供该参数)
        missing = sorted(
            p for p in required
            if p not in keys or event[p] is None or event[p] == [] or event[p] == ""
        )
        if missing:
            return CheckResult(
                status=CheckStatus.MISSING_PARAMS,
                missing_params=missing,
                model_name=model_name,
                timestamp=ts,
                reason=f"缺少模型 {model_name or family} 必需参数: {', '.join(missing)}",
            )

        return CheckResult(
            status=CheckStatus.PASS,
            model_name=model_name,
            timestamp=ts,
        )

    @staticmethod
    def _model_name(model_spec: Union[str, Dict[str, Any], None]) -> str:
        if isinstance(model_spec, str):
            return model_spec
        if isinstance(model_spec, dict):
            return str(model_spec.get("model", ""))
        return ""

    def whitelist_for(self, model_spec: Union[str, Dict[str, Any], None]) -> Dict[str, List[str]]:
        """查询指定模型/家族的白名单(required + optional)。"""
        family = resolve_family(model_spec)
        spec = self.whitelist.get(family, self.whitelist[DEFAULT_FAMILY])
        return {
            "family": family,
            "required_params": list(spec["required_params"]),
            "optional_params": list(spec.get("optional_params", [])),
        }
