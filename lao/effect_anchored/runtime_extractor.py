# v3.5.2-laorefactor: S1
"""
Runtime Extractor — LAO 架构重构 S1
===================================

安装时自动萃取 Runtime 文件（776M）→ 生成关键锚点（8-20K tokens·生产参数）。

只读扫描 Runtime 目录，不写入 Runtime 目录（不改变 Runtime 结构）。

职责:
    - 递归扫描 Runtime 目录（白名单扩展名 / 大小阈值 / 排除目录）
    - Markdown: 标题 + 决策语句 + 关键段落
    - JSON/YAML: 白名单 key 的标量 value（绝不萃取密钥）
    - Python: 仅模块 docstring + 顶层 str 常量（不萃取逻辑）
    - 锚点存入 CognitiveAnchorStore（anchor_type="runtime"）

消费方: S2 L2PreRetriever（事前检索）。

触发点:
    - 安装时: lao/init.py 或 CLI 调 extract_runtime()
    - 手动重跑: python -m lao.install --re-extract

约束: 仅标准库 · fail-open（任何异常返回空结果不抛）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from lao.effect_anchored.cognitive_anchor import Anchor, CognitiveAnchorStore


# ---------------------------------------------------------------------------
# 结果数据类
# ---------------------------------------------------------------------------

@dataclass
class ExtractionResult:
    """萃取结果摘要。"""

    anchors_added: int = 0            # 新增锚点数
    anchors_skipped: int = 0          # 跳过(重复/超限)数
    files_scanned: int = 0            # 扫描文件数
    files_extracted: int = 0          # 实际萃取文件数
    total_chars_extracted: int = 0    # 萃取总字符数
    estimated_tokens: int = 0         # 估算 token 数(总字符×0.75 折中·CJK≈1/ASCII≈0.25)
    elapsed_ms: float = 0.0           # 耗时
    errors: List[str] = field(default_factory=list)  # 失败清单(不阻塞·记录)


# ---------------------------------------------------------------------------
# 萃取器
# ---------------------------------------------------------------------------

class RuntimeExtractor:
    """安装时 Runtime 文件萃取器：Runtime 文件 → 关键锚点(生产参数)。"""

    # 文件类型白名单(值=优先级权重·SOUL.md/AGENTS.md 优先)
    FILE_WHITELIST = {
        ".md": 10,        # SOUL.md/AGENTS.md/README → 最高优先
        ".json": 5,       # 配置/状态/agents 定义
        ".yaml": 5, ".yml": 5,   # 配置
        ".py": 2,         # 仅 docstring/常量(不萃取逻辑)
    }
    MAX_FILE_BYTES = 1_000_000      # 单文件 >1MB 跳过
    MAX_DIR_BYTES = 50_000_000      # 单目录 >50MB 跳过
    SKIP_DIRS = {"node_modules", ".git", "__pycache__", "sessions",
                 "dist", "build", ".venv", "venv", "data", "logs", "tmp"}
    DECISION_KEYWORDS = ("必须", "禁止", "优先", "铁律", "always", "never",
                         "must", "never", "override", "唯一")

    # JSON/YAML 白名单 key（命中且值为 str/num → 萃取）
    JSON_KEY_WHITELIST = ("id", "agent_id", "name", "model", "provider",
                          "description", "role", "base_url", "api_base")
    # 密钥排除（key 名含以下子串 → 整个分支跳过·绝不萃取）
    SECRET_KEY_MARKERS = ("apikey", "key", "token", "secret", "password")
    # 密钥值特征（内容命中 → 丢弃候选·兜底防线）
    _SECRET_VALUE_RE = re.compile(
        r"(?i)(?:api[_-]?key|secret|password|token)\s*[:=]\s*\S|sk-[A-Za-z0-9_\-]{6,}"
    )
    _MD_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
    _YAML_LINE_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*:\s*(.+?)\s*$")
    _CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

    RECALL_LEVELS = ("high_precision", "balanced", "high_recall")

    def __init__(self, store: Optional[CognitiveAnchorStore] = None,
                 recall_level: str = "balanced"):
        """初始化萃取器。

        Args:
            store: 锚点存储；None → 内部创建临时 store(不落盘·纯计算)。
            recall_level: "high_precision"(宁缺毋滥·仅标题+决策语句) |
                          "balanced"(关键段落取 1/2) | "high_recall"(全部段落)。
                          非法值按 "balanced" 处理。
        """
        self.store = store if store is not None else CognitiveAnchorStore()
        self.recall_level = (recall_level
                             if recall_level in self.RECALL_LEVELS else "balanced")

    # -- 主流程 -----------------------------------------------------------

    def extract(self, runtime_root: str) -> ExtractionResult:
        """扫描 runtime_root → 萃取锚点 → 存入 store。返回摘要。

        fail-open：任何异常返回空 ExtractionResult 不抛。
        """
        start = time.time()
        try:
            return self._extract_impl(runtime_root, start)
        except Exception as exc:  # fail-open
            return ExtractionResult(
                elapsed_ms=(time.time() - start) * 1000.0,
                errors=[f"extract failed: {exc}"],
            )

    def _extract_impl(self, runtime_root: str, start: float) -> ExtractionResult:
        """extract 的实际执行体（已由 extract 兜底异常）。"""
        files = self._scan_files(runtime_root)
        result = ExtractionResult(files_scanned=len(files))
        seen_hashes = self._known_content_hashes()

        for path in files:
            try:
                candidates = self._extract_file(path)
            except Exception as exc:
                result.errors.append(f"{path}: {exc}")
                continue
            if not candidates:
                continue

            file_type = path.suffix.lstrip(".").lower()
            priority = self._file_priority(path)
            produced = False
            for cand in candidates:
                content = str(cand.get("content", "")).strip()
                if not content or self._looks_secret(content):
                    continue
                content_hash = hashlib.sha256(
                    content.encode("utf-8")).hexdigest()[:8]
                if content_hash in seen_hashes:
                    result.anchors_skipped += 1
                    produced = True
                    continue
                anchor = self._make_anchor(content, str(path), file_type, priority)
                try:
                    if self.store.get(anchor.anchor_id) is not None:
                        result.anchors_skipped += 1
                    else:
                        self.store.put(anchor)
                        result.anchors_added += 1
                        result.total_chars_extracted += len(content)
                        result.estimated_tokens += self.estimate_tokens(content)
                except Exception as exc:
                    result.errors.append(f"{path}: {exc}")
                    continue
                seen_hashes.add(content_hash)
                produced = True
            if produced:
                result.files_extracted += 1

        result.elapsed_ms = (time.time() - start) * 1000.0
        return result

    # -- 扫描 -------------------------------------------------------------

    def _scan_files(self, runtime_root: str) -> List[Path]:
        """递归扫描·应用白名单/大小阈值/排除目录。路径不存在返回空列表。"""
        root = Path(runtime_root)
        if not root.is_dir():
            return []
        result: List[Path] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.SKIP_DIRS]
            kept: List[Path] = []
            dir_bytes = 0
            for fname in filenames:
                path = Path(dirpath) / fname
                if path.suffix.lower() not in self.FILE_WHITELIST:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size > self.MAX_FILE_BYTES:
                    continue
                dir_bytes += size
                kept.append(path)
            if dir_bytes > self.MAX_DIR_BYTES:
                continue  # 单目录超限 → 整目录跳过
            result.extend(kept)
        return sorted(result, key=lambda p: str(p))

    # -- 分文件类型萃取 ---------------------------------------------------

    def _extract_file(self, path: Path) -> List[Dict]:
        """按扩展名分发到具体萃取器。"""
        suffix = path.suffix.lower()
        if suffix == ".md":
            return self._extract_md(path)
        if suffix == ".json":
            return self._extract_json(path)
        if suffix in (".yaml", ".yml"):
            return self._extract_yaml(path)
        if suffix == ".py":
            return self._extract_py(path)
        return []

    def _file_priority(self, path: Path) -> int:
        """计算文件优先级：SOUL/AGENTS/README=10·其他 .md=3·其余按白名单权重。"""
        if path.suffix.lower() == ".md":
            top = {"soul.md", "agents.md", "readme.md"}
            return 10 if path.name.lower() in top else 3
        return self.FILE_WHITELIST.get(path.suffix.lower(), 1)

    def _extract_md(self, path: Path) -> List[Dict]:
        """Markdown 萃取: 标题+决策语句+关键段落。返回锚点 dict 列表。"""
        text = path.read_text(encoding="utf-8", errors="replace")
        results: List[Dict] = []
        seen: set = set()

        def _add(content: str, limit: int = 200) -> None:
            """去重追加一条候选锚点内容。"""
            content = content.strip()[:limit]
            if content and content not in seen:
                seen.add(content)
                results.append({"content": content})

        for line in text.splitlines():
            if self._MD_HEADING_RE.match(line):
                _add(line)
        for sentence in re.split(r"(?<=[。！？!?\.])\s+|\n+", text):
            s = sentence.strip()
            if not s:
                continue
            low = s.lower()
            if any(k in s or k in low for k in self.DECISION_KEYWORDS):
                _add(s)
        if self.recall_level in ("balanced", "high_recall"):
            paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
            if self.recall_level == "balanced":
                paragraphs = paragraphs[::2]
            for para in paragraphs:
                _add(para)
        return results

    def _extract_json(self, path: Path) -> List[Dict]:
        """JSON 萃取: 白名单 key 的 value 对(agent id/model/provider/name 等)。"""
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return self._walk_json_like(data)

    def _extract_yaml(self, path: Path) -> List[Dict]:
        """YAML 萃取: 同 JSON 逻辑(PyYAML 可用时用之·否则简单行解析 key: value)。"""
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            import yaml  # 可选依赖·仅标准库环境走 fallback
            data = yaml.safe_load(text)
            return self._walk_json_like(data) if data is not None else []
        except ImportError:
            return self._parse_yaml_lines(text)

    def _parse_yaml_lines(self, text: str) -> List[Dict]:
        """无 PyYAML 时的简单行解析：`key: value` 标量对。"""
        results: List[Dict] = []
        for line in text.splitlines():
            m = self._YAML_LINE_RE.match(line)
            if not m:
                continue
            key, raw = m.group(1), m.group(2).strip()
            if self._is_secret_key(key):
                continue
            if key not in self.JSON_KEY_WHITELIST:
                continue
            value = raw.strip("'\"")
            if not value or value[0] in "{[&*":
                continue
            results.append({"content": f"{key}: {value}"[:200]})
        return results

    def _walk_json_like(self, data: Any) -> List[Dict]:
        """递归遍历 dict/list·命中白名单 key 且值为 str/num → 候选锚点。"""
        results: List[Dict] = []
        seen: set = set()

        def _visit(node: Any) -> None:
            """递归访问一个 JSON 节点。"""
            if isinstance(node, dict):
                for key, value in node.items():
                    key_str = str(key)
                    if self._is_secret_key(key_str):
                        continue  # 绝不萃取密钥·整分支跳过
                    if key_str in self.JSON_KEY_WHITELIST:
                        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                            content = f"{key_str}: {value}"[:200]
                            if content not in seen:
                                seen.add(content)
                                results.append({"content": content})
                    _visit(value)
            elif isinstance(node, list):
                for item in node:
                    _visit(item)

        _visit(data)
        return results

    def _extract_py(self, path: Path) -> List[Dict]:
        """Python 萃取: 仅模块 docstring + 顶层 str 常量(<200 字符)。"""
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            return []
        results: List[Dict] = []
        doc = ast.get_docstring(tree)
        if doc:
            results.append({"content": doc[:1000]})
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                    val = node.value.value
                    if isinstance(val, str) and len(val) <= 200:
                        results.append({"content": f"{target.id} = {val}"[:200]})
        return results

    # -- 锚点构造 ---------------------------------------------------------

    def _make_anchor(self, content: str, source_path: str, file_type: str,
                     priority: int) -> Anchor:
        """构造 runtime 锚点。

        anchor_id = runtime:{path_hash}:{content_hash8}
        anchor_type="runtime" · value 含 content/source_path/file_type/priority
        experience_type="agent_runtime" · tags=["runtime", file_type]
        """
        path_hash = hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:8]
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
        anchor_id = f"runtime:{path_hash}:{content_hash}"
        return Anchor(
            anchor_id=anchor_id,
            anchor_type="runtime",
            value={
                "content": content,
                "source_path": source_path,
                "file_type": file_type,
                "priority": priority,
            },
            source=source_path,
            trust_weight=0.8 if priority >= 10 else 0.6,
            tags=["runtime", file_type],
            experience_type="agent_runtime",
        )

    def _known_content_hashes(self) -> set:
        """从 store 现有 runtime 锚点提取已知内容哈希(幂等去重)。"""
        seen: set = set()
        try:
            for a in self.store.lookup(layer="runtime"):
                v = a.get("value") or {}
                c = str(v.get("content", ""))
                if c:
                    seen.add(hashlib.sha256(c.encode("utf-8")).hexdigest()[:8])
        except Exception:
            pass
        return seen

    def _looks_secret(self, content: str) -> bool:
        """内容命中密钥特征 → 丢弃(兜底防线)。"""
        return bool(self._SECRET_VALUE_RE.search(content))

    def _is_secret_key(self, key: str) -> bool:
        """key 名含密钥标记 → 跳过。"""
        kl = key.lower()
        return any(m in kl for m in self.SECRET_KEY_MARKERS)

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """CJK 字符×1 + 非 CJK 字符×0.25 估算。"""
        cjk = len(RuntimeExtractor._CJK_RE.findall(text))
        non_cjk = len(text) - cjk
        return int(cjk + non_cjk * 0.25)


def extract_runtime(runtime_root: str, store_path: Optional[str] = None,
                    recall_level: str = "balanced") -> ExtractionResult:
    """便捷入口：创建 store(可落盘)→ RuntimeExtractor.extract → 返回结果。

    供 lao/init.py 或 CLI 调用。fail-open：任何异常返回空 ExtractionResult 不抛。
    """
    try:
        store = CognitiveAnchorStore(store_path=store_path) if store_path \
            else CognitiveAnchorStore()
        return RuntimeExtractor(store=store, recall_level=recall_level).extract(runtime_root)
    except Exception as exc:
        return ExtractionResult(errors=[f"extract_runtime failed: {exc}"])
