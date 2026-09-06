"""N1 认知压缩单元测试（195号 S2/S3 交付物；对应 180号 §五 V1-V6）。

跑法（在包的父目录下）：
    python -m pytest cognitive_compression/test_n1.py -q

本文件用相对导入，因此包目录改名（本地 `module/` → 服务器 `cognitive_compression/`）
不影响测试可跑性。
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import time

import pytest

from .adapters import dsh as dsh_adapter
from .adapters import openclaw as oc
from .config import CompressionConfig, load_config
from .engine import (
    FALLBACK_BLACKLISTED,
    FALLBACK_DISABLED,
    FALLBACK_ERROR,
    FALLBACK_INTENT_UNKNOWN,
    FALLBACK_TOO_AGGRESSIVE,
    FALLBACK_TOO_FEW_TOOLS,
    CognitiveCompressor,
)
from .intent import INTENT_GENERAL, ToolIntentClassifier
from .taxonomy import ToolTaxonomy

# 9 工具样例：域覆盖 code/code/file/search/browser/fetch/media/comm/meta
TOOLS = [
    "exec",
    "apply_patch",
    "read",
    "web_search",
    "browser_control",
    "web_fetch",
    "image_generation",
    "agent_send",
    "skills",
]

TEXT_CODE = "帮我写代码，实现函数 parse_config"
TEXT_SEARCH = "搜索一下最新消息"
TEXT_MEDIA = "画一张海报"
TEXT_VAGUE = "嗯，然后呢"


def _cfg(**kw) -> CompressionConfig:
    base = dict(enabled=True, metrics_path="", context="test")
    base.update(kw)
    return CompressionConfig(**base)


def _compressor(**kw) -> CognitiveCompressor:
    return CognitiveCompressor(_cfg(**kw))


# ── 分类表 / 意图分类器 ────────────────────────────────────────────────
def test_taxonomy_explicit_and_pattern_and_unknown():
    tax = ToolTaxonomy()
    assert tax.domain_of("exec") == "code"
    assert tax.domain_of("browser_control") == "browser"
    # 显式表里没有 repo_search，靠正则命中
    assert tax.domain_of("repo_search") == "search"
    assert tax.domain_of("zzz_no_such_tool") == "unknown"
    assert tax.domain_of("") == "unknown"


def test_taxonomy_coverage_reports_unknown():
    tax = ToolTaxonomy()
    known, total, unknown = tax.coverage(TOOLS + ["zzz_no_such_tool"])
    assert total == len(TOOLS) + 1
    assert unknown == ["zzz_no_such_tool"]
    assert known == len(TOOLS)


def test_taxonomy_override_wins():
    tax = ToolTaxonomy(overrides={"exec": "meta"})
    assert tax.domain_of("exec") == "meta"
    # 非法域被忽略
    assert ToolTaxonomy(overrides={"exec": "nope"}).domain_of("exec") == "code"


def test_intent_classifier_priority_and_general():
    clf = ToolIntentClassifier()
    assert clf.classify(TEXT_CODE) == "code"
    assert clf.classify(TEXT_SEARCH) == "search"
    assert clf.classify(TEXT_MEDIA) == "media"
    assert clf.classify(TEXT_VAGUE) == INTENT_GENERAL
    assert clf.classify("") == INTENT_GENERAL
    assert clf.classify(None) == INTENT_GENERAL


def test_intent_explain_returns_hit_keyword():
    intent, kw = ToolIntentClassifier().explain(TEXT_CODE)
    assert intent == "code"
    assert kw == "写代码"


def test_intent_extra_keywords():
    clf = ToolIntentClassifier(extra_keywords={"search": ["翻一下资料库"]})
    assert clf.classify("翻一下资料库") == "search"


# ── 红线 C5：默认关闭 ──────────────────────────────────────────────────
def test_disabled_by_default_is_noop():
    comp = CognitiveCompressor(CompressionConfig())  # 出厂配置
    d = comp.decide(TOOLS, TEXT_CODE)
    assert d.fallback == FALLBACK_DISABLED
    assert d.is_noop and d.kept == tuple(TOOLS) and d.dropped == ()


def test_load_config_defaults_off(monkeypatch):
    for k in list(os.environ):
        if k.startswith("LAO_N1_"):
            monkeypatch.delenv(k, raising=False)
    cfg = load_config(context="openclaw")
    assert cfg.enabled is False
    assert (cfg.min_tools, cfg.cache_ttl_s, cfg.cache_max, cfg.blacklist_ttl_s) == (3, 300, 500, 1800)
    assert cfg.keep_unknown is True


def test_load_config_reads_env(monkeypatch):
    monkeypatch.setenv("LAO_N1_ENABLED", "1")
    monkeypatch.setenv("LAO_N1_MIN_TOOLS", "5")
    monkeypatch.setenv("LAO_N1_DROP_UNKNOWN", "1")
    monkeypatch.setenv("LAO_N1_CACHE_TTL_S", "not_a_number")
    cfg = load_config()
    assert cfg.enabled is True and cfg.min_tools == 5 and cfg.keep_unknown is False
    assert cfg.cache_ttl_s == 300  # 脏值回落默认，不抛异常


# ── 正常裁剪路径 ──────────────────────────────────────────────────────
def test_code_intent_keeps_code_and_file_domains():
    d = _compressor().decide(TOOLS, TEXT_CODE)
    assert d.fallback is None
    assert set(d.kept) == {"exec", "apply_patch", "read"}
    assert "image_generation" in d.dropped and "browser_control" in d.dropped
    assert d.original_count == len(TOOLS)
    assert d.kept_count == 3
    assert d.ratio == pytest.approx(6 / 9)
    assert d.is_noop is False


def test_kept_preserves_input_order():
    d = _compressor().decide(TOOLS, TEXT_CODE)
    assert list(d.kept) == [t for t in TOOLS if t in set(d.kept)]


# ── 180号 §4.2/§4.4 规格一致性（用规格自带的 36 工具与效果表做断言）─────────
#: 180号 §4.2 表逐行列出的 36 个工具（4+7+3+3+3+4+3+4+5）
SPEC36 = [
    "exec", "apply_patch", "diffs", "code_execution",
    "brave_search", "duckduckgo", "exa", "perplexity", "tavily", "searxng", "tool_search",
    "browser_control", "browser_login", "browser_linux_troubleshooting",
    "web_fetch", "firecrawl", "web",
    "pdf", "image_read", "file_ops",
    "image_generation", "video_generation", "music_generation", "tts",
    "agent_send", "acp_agents", "subagents",
    "skills", "slash_commands", "steer", "loop_detection",
    "llm_task", "thinking", "tokenjuice", "reactions", "trajectory",
]


def test_spec36_domain_counts_match_180_table():
    """分类表的域分布必须与 180号 §4.2 表的「预估工具数」逐格相等。"""
    assert len(SPEC36) == 36 and len(set(SPEC36)) == 36
    tax = ToolTaxonomy()
    known, total, unknown = tax.coverage(SPEC36)
    assert (known, total, unknown) == (36, 36, [])

    from collections import Counter

    counts = Counter(tax.domain_of(t) for t in SPEC36)
    assert dict(counts) == {
        "code": 4,
        "search": 7,
        "browser": 3,
        "fetch": 3,
        "file": 3,
        "media": 4,
        "comm": 3,
        "meta": 4,
        "common": 5,
    }


@pytest.mark.parametrize(
    "text, intent, expected_kept",
    [
        ("心跳检查", "chat", 4),      # 180号 §4.4 效果表：保留域数 1 / 3-5 工具
        (TEXT_CODE, "code", 7),        # 表：2 域 / 6-8 工具
        (TEXT_SEARCH, "search", 7),    # 表：1 域 / 5-7 工具
        ("打开网页并截图", "browse", 3),  # 表：1 域 / 3 工具
        (TEXT_MEDIA, "media", 4),      # 表：1 域 / 3-4 工具
    ],
)
def test_spec36_kept_counts_match_180_effect_table(text, intent, expected_kept):
    d = _compressor().decide(SPEC36, text)
    assert d.intent == intent
    assert d.fallback is None
    assert d.kept_count == expected_kept


def test_general_intent_keeps_all_36():
    """180号 §4.4 效果表最后一行：general → 8 域 / 36 工具 / 压缩率 0%。"""
    d = _compressor().decide(SPEC36, TEXT_VAGUE)
    assert d.intent == INTENT_GENERAL
    assert d.kept_count == 36 and d.ratio == 0.0


def test_common_domain_is_pruned_by_default():
    """§4.2「通用」5 工具不属任何 intent 的 kept_domains，默认全意图被裁。"""
    d = _compressor().decide(SPEC36, "心跳检查")
    assert set(d.kept) == {"skills", "slash_commands", "steer", "loop_detection"}
    assert {"thinking", "llm_task", "tokenjuice", "reactions", "trajectory"} <= set(d.dropped)


def test_always_keep_domains_switch_retains_common():
    """一个开关即可把「通用」域转为常留，无需改分类表。"""
    comp = _compressor(always_keep_domains=frozenset({"common"}))
    d = comp.decide(SPEC36, "心跳检查")
    assert d.kept_count == 9
    assert "thinking" in d.kept and "tokenjuice" in d.kept


def test_always_keep_domains_from_env(monkeypatch):
    monkeypatch.setenv("LAO_N1_ALWAYS_KEEP_DOMAINS", "common, meta")
    assert load_config().always_keep_domains == frozenset({"common", "meta"})
    monkeypatch.delenv("LAO_N1_ALWAYS_KEEP_DOMAINS")
    assert load_config().always_keep_domains == frozenset()


def test_browse_intent_does_not_leak_into_fetch_domain():
    """browse 只留 browser（180号 §4.3 表），fetch 域工具须被裁。"""
    d = _compressor().decide(SPEC36, "打开网页并截图")
    assert set(d.kept) == {"browser_control", "browser_login", "browser_linux_troubleshooting"}
    assert {"web_fetch", "firecrawl", "web"} <= set(d.dropped)


# ── DSH 语境真实枚举（191号实证）────────────────────────────────
#: 证据：probe/evidence/baseline.jsonl 的 toolNames，toolCount=28。
#: 命名与 OpenClaw 完全不同族——这就是 195号 §3.2「两套基线不可混算」的具体原因。
DSH28 = [
    "bash", "create_goal", "edit", "exit_plan_mode", "get_goal", "glob", "grep",
    "interrupt_agent", "job_kill", "job_list", "job_output", "lao_probe_blocked",
    "lao_probe_echo", "list_agents", "ralph", "read", "read_image", "send_message",
    "skill", "str_replace_editor", "subagent", "subagent_fork", "todo_write",
    "update_goal", "web_fetch", "web_search", "workflow", "write",
]


def test_dsh28_classification_is_exact():
    """DSH 28 工具逐个核对。两个探针工具应落 unknown（不入产线表）。"""
    assert len(DSH28) == 28 and len(set(DSH28)) == 28
    tax = ToolTaxonomy()
    known, total, unknown = tax.coverage(DSH28)
    assert total == 28
    assert unknown == ["lao_probe_blocked", "lao_probe_echo"]
    assert known == 26

    from collections import Counter

    assert dict(Counter(tax.domain_of(t) for t in DSH28)) == {
        "code": 1,       # bash
        "file": 5,       # edit read read_image str_replace_editor write
        "search": 3,     # glob grep web_search
        "fetch": 1,      # web_fetch
        "comm": 5,       # interrupt_agent list_agents send_message subagent subagent_fork
        "meta": 11,      # create_goal exit_plan_mode get_goal job_* ralph skill todo_write update_goal workflow
        "unknown": 2,    # lao_probe_*
    }


def test_dsh28_code_intent_matches_191_field_result():
    """DSH 侧 code 意图：留 code+file+两个 unknown 探针，量级与 191号实测一致。

    191号 headless 实测：28 工具 / 32,829 字符 → restrict 到 5 工具 / 6,821 字符（降 79.2%）。
    本引擎在同一工具集上留 8 个（含 2 个为安全而保留的未知探针），同量级。
    字符级降幅需 S4' 插件实测后回填，不在本层断言。
    """
    d = _compressor().decide(DSH28, TEXT_CODE)
    assert d.fallback is None
    assert set(d.kept) == {
        "bash", "edit", "read", "read_image", "str_replace_editor", "write",
        "lao_probe_blocked", "lao_probe_echo",
    }
    assert d.ratio == pytest.approx(20 / 28)


def test_dsh28_search_intent_keeps_search_domain():
    d = _compressor().decide(DSH28, TEXT_SEARCH)
    assert d.fallback is None
    assert {"glob", "grep", "web_search"} <= set(d.kept)
    assert "bash" in d.dropped and "send_message" in d.dropped


# ── 三道兜底 ──────────────────────────────────────────────────────────
def test_fallback_too_few_tools():
    d = _compressor().decide(["exec", "read", "skills"], TEXT_CODE)
    assert d.fallback == FALLBACK_TOO_FEW_TOOLS and d.dropped == ()


def test_fallback1_intent_unknown_keeps_all():
    d = _compressor().decide(TOOLS, TEXT_VAGUE)
    assert d.fallback == FALLBACK_INTENT_UNKNOWN
    assert d.kept == tuple(TOOLS) and d.dropped == ()


def test_fallback2_too_aggressive_keeps_all():
    # search 意图只留 web_search 一个 → 少于 min_tools=3 → 整体回退
    d = _compressor(keep_unknown=False).decide(TOOLS, TEXT_SEARCH)
    assert d.fallback == FALLBACK_TOO_AGGRESSIVE
    assert d.kept == tuple(TOOLS) and d.dropped == ()


def test_fallback3_blacklist_blocks_same_intent():
    comp = _compressor()
    first = comp.decide(TOOLS, TEXT_CODE)
    assert first.fallback is None

    until = comp.report_misprune("code", "image_generation")
    assert until > time.time()
    assert "code" in comp.blacklist_snapshot()
    # 兜底 3 生效：同意图不再裁
    again = comp.decide(TOOLS, TEXT_CODE)
    assert again.fallback == FALLBACK_BLACKLISTED and again.dropped == ()
    # 误裁上报会清缓存，避免旧决策被复用
    assert comp.cache_size() == 0


def test_blacklist_only_affects_reported_intent():
    comp = _compressor()
    # media 在 9 工具样例里只剩 image_generation 一个，本就会走兜底 2；
    # 要验证「黑名单不注其他意图」就得让 media 域自身够数。
    tools = TOOLS + ["tts", "video_generation"]
    comp.report_misprune("code")
    assert comp.decide(tools, TEXT_CODE).fallback == FALLBACK_BLACKLISTED
    media = comp.decide(tools, TEXT_MEDIA)
    assert media.fallback is None
    assert set(media.kept) == {"image_generation", "tts", "video_generation"}


def test_blacklist_expires_after_ttl():
    comp = _compressor()
    comp.report_misprune("code")
    # 直接把解禁时刻拨到过去，等价于 TTL 到期
    comp._blacklist._until["code"] = time.time() - 1
    assert comp.decide(TOOLS, TEXT_CODE).fallback is None


def test_reset_clears_cache_and_blacklist():
    comp = _compressor()
    comp.decide(TOOLS, TEXT_CODE)
    comp.report_misprune("media")
    comp.reset()
    assert comp.cache_size() == 0 and comp.blacklist_snapshot() == {}


# ── 缓存 ──────────────────────────────────────────────────────────────
def test_cache_hit_on_second_identical_call():
    comp = _compressor()
    first = comp.decide(TOOLS, TEXT_CODE)
    second = comp.decide(TOOLS, "再写个脚本吧")  # 同为 code 意图、同工具集
    assert first.cache_hit is False and second.cache_hit is True
    assert second.kept == first.kept
    assert comp.cache_size() == 1


def test_cache_key_is_order_insensitive():
    comp = _compressor()
    k1 = comp._cache_key("code", ["a", "b", "c"])
    k2 = comp._cache_key("code", ["c", "a", "b"])
    assert k1 == k2 and len(k1) == 16
    assert comp._cache_key("search", ["a", "b", "c"]) != k1


def test_cache_expires_after_ttl():
    comp = _compressor()
    comp.decide(TOOLS, TEXT_CODE)
    key = comp._cache_key("code", TOOLS)
    stored_at, kept = comp._cache._data[key]
    comp._cache._data[key] = (stored_at - comp.config.cache_ttl_s - 1, kept)
    assert comp.decide(TOOLS, TEXT_CODE).cache_hit is False


def test_cache_disabled_when_ttl_zero():
    comp = _compressor(cache_ttl_s=0)
    comp.decide(TOOLS, TEXT_CODE)
    assert comp.cache_size() == 0
    assert comp.decide(TOOLS, TEXT_CODE).cache_hit is False


def test_cache_lru_evicts_oldest():
    comp = _compressor(cache_max=2)
    comp.decide(TOOLS, TEXT_CODE)
    comp.decide(TOOLS, TEXT_MEDIA)
    comp.decide(TOOLS + ["extra_exec"], TEXT_CODE)
    assert comp.cache_size() == 2


def test_cache_hit_rechecks_min_tools():
    """缓存里的名单在宿主工具集变化后可能不再够数 → 仍须走兜底 2。"""
    comp = _compressor()
    comp.decide(TOOLS, TEXT_CODE)
    key = comp._cache_key("code", TOOLS)
    stored_at, _ = comp._cache._data[key]
    comp._cache._data[key] = (stored_at, ("exec",))
    d = comp.decide(TOOLS, TEXT_CODE)
    assert d.cache_hit is True and d.fallback == FALLBACK_TOO_AGGRESSIVE


# ── keep_unknown（源码漂移防护）─────────────────────────────────────────
def test_keep_unknown_true_retains_unrecognised_tool():
    d = _compressor().decide(TOOLS + ["zzz_no_such_tool"], TEXT_CODE)
    assert "zzz_no_such_tool" in d.kept


def test_keep_unknown_false_drops_unrecognised_tool():
    d = _compressor(keep_unknown=False).decide(TOOLS + ["zzz_no_such_tool"], TEXT_CODE)
    assert "zzz_no_such_tool" in d.dropped


# ── fail-open ─────────────────────────────────────────────────────────
class _BoomClassifier:
    def classify(self, text):  # noqa: D401
        raise RuntimeError("boom")


def test_fail_open_on_internal_error():
    comp = CognitiveCompressor(_cfg(), classifier=_BoomClassifier())
    d = comp.decide(TOOLS, TEXT_CODE)
    assert d.fallback == FALLBACK_ERROR
    assert d.kept == tuple(TOOLS) and d.dropped == ()


def test_empty_and_none_tool_list_are_safe():
    comp = _compressor()
    assert comp.decide([], TEXT_CODE).fallback == FALLBACK_TOO_FEW_TOOLS
    assert comp.decide(None, TEXT_CODE).fallback == FALLBACK_TOO_FEW_TOOLS


# ── OpenClaw 适配器 ───────────────────────────────────────────────────
def _payload(names=None, text=TEXT_CODE):
    names = names if names is not None else TOOLS
    return {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "你可以搜索、画图、浏览网页、执行代码"},
            {"role": "user", "content": text},
        ],
        "tools": [
            {"type": "function", "function": {"name": n, "parameters": {"type": "object"}}}
            for n in names
        ],
    }


def test_openclaw_extracts_names_from_both_shapes():
    tools = [
        {"type": "function", "function": {"name": "exec"}},
        {"name": "read"},
        {"garbage": 1},
        "not_a_dict",
    ]
    assert oc.extract_tool_names(tools) == ["exec", "read", "", ""]
    assert oc.extract_tool_names(None) == []


def test_openclaw_user_text_ignores_system_message():
    p = _payload()
    text = oc.iter_user_text(p["messages"])
    assert text == TEXT_CODE
    assert "画图" not in text  # system 里的工具罗列不得污染意图判定


def test_openclaw_user_text_handles_content_parts():
    messages = [{"role": "user", "content": [{"type": "text", "text": "写代码"}, {"type": "image"}]}]
    assert oc.iter_user_text(messages) == "写代码"


def test_openclaw_prunes_tools_and_never_touches_messages():
    comp = _compressor()
    p = _payload()
    messages_before = copy.deepcopy(p["messages"])
    d = oc.compress_payload(p, comp, agent="tester", request_id="r1")
    assert d.fallback is None
    assert oc.extract_tool_names(p["tools"]) == ["exec", "apply_patch", "read"]
    assert p["messages"] == messages_before  # 红线 C1/C3
    assert set(p.keys()) == {"model", "messages", "tools"}


def test_openclaw_noop_leaves_payload_object_untouched():
    comp = _compressor()
    p = _payload(text=TEXT_VAGUE)
    tools_obj = p["tools"]
    d = oc.compress_payload(p, comp)
    assert d.fallback == FALLBACK_INTENT_UNKNOWN
    assert p["tools"] is tools_obj  # 同一对象，连重建都没有


def test_openclaw_disabled_is_transparent():
    comp = CognitiveCompressor(CompressionConfig())
    p = _payload()
    before = copy.deepcopy(p)
    assert oc.compress_payload(p, comp).fallback == FALLBACK_DISABLED
    assert p == before


def test_openclaw_payload_without_tools_is_safe():
    comp = _compressor()
    p = {"model": "m", "messages": [{"role": "user", "content": TEXT_CODE}]}
    d = oc.compress_payload(p, comp)
    assert d.fallback == FALLBACK_TOO_FEW_TOOLS
    assert "tools" not in p


def test_openclaw_duplicate_tool_name_handled_consistently():
    """重名工具：两份必然同留同弃，条数自洽，不触发防御性回退。

    （本用例本是为验证 compress_payload 里的条数不变量而写，实测证明该分支
    按当前实现不可达；此处改为锁住「重名不会造成错裁」这个真实行为。）
    """
    comp = _compressor()
    p = _payload(names=TOOLS + ["exec"])  # exec 重名
    d = oc.compress_payload(p, comp)
    assert d.fallback is None
    assert oc.extract_tool_names(p["tools"]) == ["exec", "apply_patch", "read", "exec"]
    assert len(p["tools"]) == d.kept_count


def test_openclaw_detect_misprune():
    resp = {
        "choices": [
            {"message": {"tool_calls": [{"function": {"name": "image_generation"}}]}}
        ]
    }
    hit, name = oc.detect_misprune(resp, ["image_generation", "web_search"])
    assert hit is True and name == "image_generation"
    assert oc.detect_misprune(resp, ["exec"]) == (False, "")
    assert oc.detect_misprune(None, ["exec"]) == (False, "")
    assert oc.detect_misprune(resp, []) == (False, "")


# ── DSH 适配器 ────────────────────────────────────────────────────────
def test_dsh_allow_list_on_prune():
    comp = _compressor()
    allow = dsh_adapter.build_allow_list(TOOLS, TEXT_CODE, comp, agent="dsh-agent")
    assert allow == ["exec", "apply_patch", "read"]
    assert dsh_adapter.restrict_args(allow) == {"allow": allow}


def test_dsh_returns_none_on_noop_so_restrict_is_not_called():
    comp = _compressor()
    assert dsh_adapter.build_allow_list(TOOLS, TEXT_VAGUE, comp) is None
    assert dsh_adapter.restrict_args(None) is None


def test_dsh_decision_response_shape():
    comp = _compressor()
    d = dsh_adapter.decide(TOOLS, TEXT_CODE, comp)
    body = dsh_adapter.decision_response(d)
    assert body["restrict"] is True
    assert body["allow"] == ["exec", "apply_patch", "read"]
    assert "image_generation" in body["dropped"]
    assert body["intent"] == "code" and body["fallback"] is None

    noop = dsh_adapter.decide(TOOLS, TEXT_VAGUE, comp)
    body2 = dsh_adapter.decision_response(noop)
    assert body2["restrict"] is False and body2["allow"] == []


def test_dsh_report_misprune_blacklists_and_logs():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "n1.jsonl")
        comp = CognitiveCompressor(_cfg(metrics_path=path))
        until = dsh_adapter.report_misprune(comp, "code", "web_fetch")
        assert until > time.time()
        rows = [json.loads(l) for l in open(path, encoding="utf-8")]
        assert rows[-1]["event"] == "misprune_reported"
        assert rows[-1]["context"] == "dsh" and rows[-1]["tool"] == "web_fetch"


# ── 指标 ──────────────────────────────────────────────────────────────
def test_metrics_record_written_with_context_field():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "sub", "n1.jsonl")
        comp = CognitiveCompressor(_cfg(metrics_path=path))
        p = _payload()
        oc.compress_payload(p, comp, agent="a1", request_id="req-9")
        rows = [json.loads(l) for l in open(path, encoding="utf-8")]
        assert len(rows) == 1
        r = rows[0]
        assert r["context"] == "openclaw" and r["adapter"] == "openclaw.payload_tools"
        assert r["module"] == "n1_cognitive_compression"
        assert r["tools_before"] == 9 and r["tools_after"] == 3
        assert r["chars_before"] > r["chars_after"] > 0
        assert 0 < r["compression_ratio"] < 1
        assert r["ratio_basis"] == "chars"
        assert r["fallback"] is None and r["agent"] == "a1" and r["request_id"] == "req-9"
        assert set(r["dropped_tools"]) == set(TOOLS) - {"exec", "apply_patch", "read"}


def test_metrics_not_written_when_path_empty():
    comp = _compressor(metrics_path="")
    p = _payload()
    oc.compress_payload(p, comp)  # 不抛异常即通过

    from .metrics import emit

    assert emit("", {"a": 1}) is False


def test_metrics_ratio_falls_back_to_tool_count():
    from .metrics import build_record

    comp = _compressor()
    d = comp.decide(TOOLS, TEXT_CODE)
    rec = build_record(d, context="dsh", adapter="x", chars_before=0, chars_after=0)
    assert rec["compression_ratio"] == pytest.approx(round(6 / 9, 4))
    assert rec["ratio_basis"] == "tools"
    assert rec["chars_after"] is None


def test_metrics_unmeasured_chars_after_must_not_report_full_compression():
    """DSH 语境只知 chars_before（restrict 在插件侧执行）。

    若把未测得的 chars_after 当成 0，(before-0)/before 会报 100% 压缩，
    使 180号 §4.7「压缩率<40% 告警」在 DSH 线上永不可能触发。
    此处锁住：不传 chars_after 时必须退回工具条数口径，且显式标注 basis。
    """
    from .metrics import build_record

    comp = _compressor()
    d = comp.decide(DSH28, TEXT_CODE)
    rec = build_record(d, context="dsh", adapter="dsh.tools_restrict",
                       chars_before=32829)
    assert rec["compression_ratio"] != 1.0
    assert rec["compression_ratio"] == pytest.approx(round(d.ratio, 4))
    assert rec["chars_before"] == 32829
    assert rec["chars_after"] is None
    assert rec["ratio_basis"] == "tools"


def test_metrics_measured_chars_after_zero_is_distinguishable():
    """真正实测到 0 字符（tools 被清空）与「未测得」必须可区分。"""
    from .metrics import build_record

    comp = _compressor()
    d = comp.decide(TOOLS, TEXT_CODE)
    rec = build_record(d, context="openclaw", adapter="x",
                       chars_before=1000, chars_after=0)
    assert rec["compression_ratio"] == 1.0
    assert rec["chars_after"] == 0
    assert rec["ratio_basis"] == "chars"


def test_dsh_adapter_records_tools_basis_ratio():
    """端到端：DSH 适配器落盘的指标不得出现虚假 100%。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "dsh.jsonl")
        comp = CognitiveCompressor(_cfg(metrics_path=path))
        allow = dsh_adapter.build_allow_list(
            DSH28, TEXT_CODE, comp, agent="dsh-a", request_id="rid",
            chars_before=32829, metrics_path=path)
        assert allow is not None
        rows = [json.loads(l) for l in open(path, encoding="utf-8")]
        r = rows[-1]
        assert r["context"] == "dsh"
        assert r["compression_ratio"] != 1.0
        assert r["ratio_basis"] == "tools"
        assert r["chars_after"] is None
