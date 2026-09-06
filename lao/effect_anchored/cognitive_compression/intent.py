"""ToolIntentClassifier —— 工具意图分类器（180号 §4.3）。

复用 `routing/task_classifier.py` 的关键词匹配范式：纯字符串包含判定，零 LLM 依赖、
零网络、零外部状态。输出 8 类工具意图，其中 general 表示「判不准」，由核心引擎按
兜底 1 处理为不裁剪（180号 §4.6）。

与 TaskClassifier 的关系：
    TaskClassifier 判「任务有多重」（决定选哪个模型），本类判「任务要用哪类工具」
    （决定留哪些 schema）。两者正交，故新建而非改造——180号 §4.3 原文即此要求。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

#: 意图 → 触发关键词。按 INTENT_PRIORITY 顺序匹配，命中即返回。
INTENT_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "media": (
        "画图", "画一张", "生成图片", "生成图像", "配图", "海报", "插画",
        "生成视频", "视频生成", "剪辑", "配音", "朗读", "语音合成", "念一下",
        "生成音乐", "作曲", "tts", "image generation", "video generation",
        "text to speech", "draw ", "generate an image",
    ),
    "browse": (
        "打开网页", "打开这个网站", "浏览器", "登录网站", "点一下", "点击",
        "填表", "截图", "网页操作", "browser", "screenshot", "click ",
    ),
    "fetch": (
        "抓取", "爬取", "爬一下", "读这个url", "读这个链接", "把这个网页",
        "网页内容", "正文提取", "fetch ", "crawl", "scrape", "download the page",
    ),
    "search": (
        "搜索", "搜一下", "查一下", "查查", "检索", "调研", "找一下资料",
        "最新消息", "新闻", "search", "look up", "google", "find information",
        "查资料", "grep", "全文搜索",
    ),
    "code": (
        "写代码", "编程", "实现函数", "写一个脚本", "写个脚本", "代码生成",
        "python", "javascript", "typescript", "写函数", "写类", "写接口",
        "写api", "写测试", "单元测试", "重构", "调试", "报错", "堆栈",
        "改一下代码", "跑一下", "执行一下", "打补丁", "code", "function",
        "refactor", "debug", "traceback", "compile", "unit test",
    ),
    "comm": (
        "派单", "派给", "通知", "转告", "告诉", "发消息", "叫上", "协同",
        "调用agent", "让agent", "子代理", "subagent", "dispatch", "notify",
    ),
    "chat": (
        "心跳", "心跳检查", "状态检查", "ping", "hi", "hello", "你好",
        "在吗", "heartbeat", "健康检查", "health check",
    ),
}

#: 匹配优先级：越具体的意图越靠前，避免被泛化词吞掉。
#: media/browse/fetch 先于 search（"搜索图片" 属 media 而非 search 的语义歧义
#: 由具体词优先解决）；code 先于 comm；chat 最后（其词最短最易误命中）。
INTENT_PRIORITY: Tuple[str, ...] = (
    "media",
    "browse",
    "fetch",
    "search",
    "code",
    "comm",
    "chat",
)

#: 判不准时的出口。核心引擎见此即 noop（180号 §4.6 兜底 1）。
INTENT_GENERAL = "general"


class ToolIntentClassifier:
    """关键词工具意图分类器。

    Args:
        extra_keywords: 追加关键词 {意图: [词, ...]}，用于宿主侧补词而不改本文件。
    """

    def __init__(self, extra_keywords: Dict[str, List[str]] | None = None) -> None:
        self._kw: Dict[str, Tuple[str, ...]] = {k: tuple(v) for k, v in INTENT_KEYWORDS.items()}
        if extra_keywords:
            for intent, words in extra_keywords.items():
                if intent in self._kw:
                    self._kw[intent] = self._kw[intent] + tuple(str(w).lower() for w in words)

    def classify(self, text: str) -> str:
        """判定工具意图。

        Args:
            text: 用户文本（已由调用方拼接，本方法不修改也不回传）。

        Returns:
            INTENT_PRIORITY 中的一个，或 INTENT_GENERAL。
        """
        if not text or not str(text).strip():
            return INTENT_GENERAL
        low = str(text).lower()
        for intent in INTENT_PRIORITY:
            for kw in self._kw.get(intent, ()):
                if kw in low:
                    return intent
        return INTENT_GENERAL

    def explain(self, text: str) -> Tuple[str, str]:
        """返回 (意图, 命中的关键词)，用于误裁复盘与单测断言。"""
        if not text or not str(text).strip():
            return INTENT_GENERAL, ""
        low = str(text).lower()
        for intent in INTENT_PRIORITY:
            for kw in self._kw.get(intent, ()):
                if kw in low:
                    return intent, kw
        return INTENT_GENERAL, ""
