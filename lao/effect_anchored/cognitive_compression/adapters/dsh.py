"""DSH / Cordis 语境适配器（191号实证：走 `agent.ctx.tools.restrict({allow})`）。

与 OpenClaw 侧的本质差别（191号 §四实证结论）：
    DSH 里没有 `payload["tools"]` 可裁。工具集是框架在 `agent/session-start` 时装配的，
    唯一合法收窄手段是 `agent.ctx.tools.restrict({ allow: [...] })`——给白名单，框架自己
    重算暴露面。因此本适配器**不裁数组，只产名单**。

跨语言的决策归属（本次核定）：
    DSH 插件是 TypeScript，核心引擎是 Python。若在插件里重写一份分类逻辑，两份关键词表
    与八域表必然漂移，180号 §七「工具分类与源码漂移」风险会翻倍。故核定：
        决策权只留在 Python 引擎一处，DSH 插件通过 LAO 的决策端点取回 allow 名单。
    本文件提供的就是该端点的服务侧实现（返回值已是 `restrict()` 的入参形状）。
    端点注册与插件侧 HTTP 调用属 S4'，其超时/失败必须 fail-open：取不到名单就不 restrict。

基线口径（不可与 OpenClaw 混算）：
    DSH headless 实测 32,829 字符 / 28 工具 → restrict 后 6,821 字符 / 5 工具（降 79.2%）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from ..engine import CognitiveCompressor, CompressionDecision
from ..metrics import record_decision

#: 指标里的语境标识。
DSH_CONTEXT = "dsh"
_ADAPTER = "dsh.tools_restrict"


def build_allow_list(
    tool_names: Sequence[str],
    user_text: str,
    compressor: CognitiveCompressor,
    *,
    agent: str = "",
    request_id: str = "",
    chars_before: int = 0,
    metrics_path: Optional[str] = None,
) -> Optional[List[str]]:
    """算出 restrict 白名单。

    Args:
        tool_names: 插件在 session-start 时枚举到的全部工具名。
        user_text: 用户意图文本（插件侧从首轮消息取，只读）。
        compressor: 引擎实例。
        agent: 归因 agent。
        request_id: 会话/请求 id。
        chars_before: 插件实测的裁前 schema 字符数（可选，仅进指标）。
        metrics_path: 指标文件；None 时用 compressor.config.metrics_path。

    Returns:
        允许保留的工具名列表；**None 表示不要调用 restrict()**（回退 / 无需裁剪）。
        返回 None 而不是全量名单，是为了让插件侧走「完全不动框架」的路径——见 191号
        红线 C2「回退时不得留下任何调用痕迹」。
    """
    decision = decide(
        tool_names,
        user_text,
        compressor,
        agent=agent,
        request_id=request_id,
        chars_before=chars_before,
        metrics_path=metrics_path,
    )
    if decision.is_noop:
        return None
    return list(decision.kept)


def decide(
    tool_names: Sequence[str],
    user_text: str,
    compressor: CognitiveCompressor,
    *,
    agent: str = "",
    request_id: str = "",
    chars_before: int = 0,
    chars_after: Optional[int] = None,
    metrics_path: Optional[str] = None,
) -> CompressionDecision:
    """做决策并落指标，返回完整决策对象（需要 dropped 名单时用这个）。

    DSH 侧的 chars_after 只能由插件实测后回传（restrict() 在插件侧执行，框架
    重算暴露面之后才能量）。未回传时必须保持 None 而不能写 0：0 是合法的
    实测值（tools 被清空），metrics 无法区分二者，会把压缩率算成 100%。
    传 None 时 compression_ratio 退回按工具数计，ratio_basis 标为 "tools"。
    """
    decision = compressor.decide(tool_names, user_text)
    path = compressor.config.metrics_path if metrics_path is None else metrics_path
    if path:
        record_decision(
            path,
            decision,
            context=DSH_CONTEXT,
            adapter=_ADAPTER,
            chars_before=chars_before,
            chars_after=chars_after,
            agent=agent,
            request_id=request_id,
        )
    return decision


def restrict_args(allow: Optional[Sequence[str]]) -> Optional[Dict[str, List[str]]]:
    """把白名单包成 `restrict()` 的入参形状。

    Returns:
        `{"allow": [...]}`，或 None（不应调用 restrict）。
    """
    if allow is None:
        return None
    return {"allow": [str(a) for a in allow]}


def decision_response(decision: CompressionDecision) -> Dict[str, Any]:
    """决策端点的 JSON 响应体（插件侧据此决定是否 restrict）。

    字段含义：
        restrict  —— 是否应调用 restrict()
        allow     —— restrict 入参名单（restrict=False 时为空）
        dropped   —— 被裁名单，插件须留存以支持兜底 3 的误裁上报
        intent / fallback —— 供插件侧日志归因，不参与控制流
    """
    return {
        "restrict": not decision.is_noop,
        "allow": list(decision.kept) if not decision.is_noop else [],
        "dropped": list(decision.dropped),
        "intent": decision.intent,
        "fallback": decision.fallback,
        "cache_hit": decision.cache_hit,
        "latency_ms": round(decision.latency_ms, 3),
    }


def report_misprune(
    compressor: CognitiveCompressor,
    intent: str,
    tool_name: str = "",
    *,
    metrics_path: Optional[str] = None,
) -> float:
    """插件侧检测到被裁工具被调用时的上报入口（对应 191号 R8 / V14）。

    DSH 侧没有 OpenAI 响应体可查 tool_calls，误裁信号来自框架的「未知工具」错误或
    Agent 显式抱怨；插件捕获后调本函数，该意图即进黑名单，后续会话不再 restrict。

    Returns:
        黑名单解禁时间戳（epoch 秒）。
    """
    until = compressor.report_misprune(intent, tool_name)
    path = compressor.config.metrics_path if metrics_path is None else metrics_path
    if path:
        from ..metrics import emit
        import time as _time

        emit(
            path,
            {
                "ts": _time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "module": "n1_cognitive_compression",
                "context": DSH_CONTEXT,
                "adapter": _ADAPTER,
                "event": "misprune_reported",
                "intent": intent,
                "tool": tool_name,
                "blacklist_until": until,
            },
        )
    return until
