# LAO 架构重构 · S1 代码规格 · runtime_extractor.py

> **版本:** v1.0 | **日期:** 2026-08-19 10:58 CST
> **依据:** 创始人 10:35 令 + 10:53 确认 + 技术拆解方案 v0.1 子任务1
> **执行:** Qoder（GLM-5.3）| **验收:** Tristan → Shuyu 二次验收
> **约束:** 仅标准库 · 所有函数 docstring · 模块头注释追加 `# v3.5.2-laorefactor: S1` · fail-open · 不改变现有代码

---

## 一、模块定位

**模块路径:** `lao/effect_anchored/runtime_extractor.py`（新建）

**职责:** 安装时自动萃取 Runtime 文件（776M）→ 生成关键锚点（8-20K tokens·生产参数）。只读扫描 Runtime 目录，**不写入 Runtime 目录**（不改变 Runtime 结构）。

**触发点:** ① 安装时（lao/init.py 或 cli 调 `extract_runtime()`）② 手动重跑（`python -m lao.install --re-extract`）

**输出消费方:** S2 L2PreRetriever（事前检索）· 锚点存入 CognitiveAnchorStore（`anchor_type="runtime"`）。

---

## 二、类与函数签名

### 2.1 数据类 `ExtractionResult`

```python
@dataclass
class ExtractionResult:
    """萃取结果摘要。"""
    anchors_added: int            # 新增锚点数
    anchors_skipped: int          # 跳过(重复/超限)数
    files_scanned: int            # 扫描文件数
    files_extracted: int          # 实际萃取文件数
    total_chars_extracted: int    # 萃取总字符数
    estimated_tokens: int         # 估算 token 数(总字符×0.75 折中·CJK≈1/ASCII≈0.25)
    elapsed_ms: float             # 耗时
    errors: List[str]             # 失败清单(不阻塞·记录)
```

### 2.2 主类 `RuntimeExtractor`

```python
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

    def __init__(self, store: Optional[CognitiveAnchorStore] = None,
                 recall_level: str = "balanced"):
        # store=None → 内部创建临时 store(不落盘·纯计算)
        # recall_level: "high_precision"(默认·宁缺毋滥) | "balanced" | "high_recall"
        ...

    def extract(self, runtime_root: str) -> ExtractionResult:
        """扫描 runtime_root → 萃取锚点 → 存入 store。返回摘要。"""
        ...

    def _scan_files(self, runtime_root: str) -> List[Path]:
        """递归扫描·应用白名单/大小阈值/排除目录。"""
        ...

    def _extract_md(self, path: Path) -> List[Dict]:
        """Markdown 萃取: 标题+决策语句+关键段落。返回锚点 dict 列表。"""
        ...

    def _extract_json(self, path: Path) -> List[Dict]:
        """JSON 萃取: 白名单 key 的 value 对(agent id/model/provider/name 等)。"""
        ...

    def _extract_py(self, path: Path) -> List[Dict]:
        """Python 萃取: 仅模块 docstring + 顶层常量(str 值·<200 字符)。"""
        ...

    def _make_anchor(self, content: str, source_path: str, file_type: str,
                     priority: int) -> Anchor:
        """构造 runtime 锚点。anchor_id = runtime:{path_hash}:{content_hash8}。
        anchor_type="runtime" · value={"content":..., "source_path":..., "file_type":...,
        "priority":...} · experience_type="agent_runtime" · tags=["runtime", file_type]"""
        ...

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """CJK 字符×1 + 非 CJK 字符×0.25 估算。"""
        ...
```

### 2.3 模块级便捷函数

```python
def extract_runtime(runtime_root: str, store_path: Optional[str] = None,
                    recall_level: str = "balanced") -> ExtractionResult:
    """便捷入口：创建 store(可落盘)→ RuntimeExtractor.extract → 返回结果。
    供 lao/init.py 或 CLI 调用。fail-open：任何异常返回空 ExtractionResult 不抛。"""
```

---

## 三、萃取规则细节

### 3.1 Markdown 萃取（`.md`）
1. **标题行**（`#`/`##`/`###` 开头）→ 作为锚点 content（短·高信号）
2. **决策语句**：含 DECISION_KEYWORDS 的完整句子 → 锚点 content
3. **关键段落**：`recall_level="high_recall"` 时·每段 ≤200 字符截断萃取；`balanced` 时仅 1/2
4. **优先级**：`SOUL.md`/`AGENTS.md` 权重 10 最高（Shuyu 优化①）·`README.md` 权重 10·其他 .md 权重 3
5. 同文件多段 → 每段一个锚点（content_hash 去重·同内容全局只存一份）

### 3.2 JSON 萃取（`.json`）
- 白名单 key：`id` / `agent_id` / `name` / `model` / `provider` / `description` / `role` / `base_url` / `api_base`
- 递归遍历 JSON·命中白名单 key 且值为 str/num → 锚点
- 排除：`apiKey` / `key` / `token` / `secret` / `password`（**绝不萃取密钥**）

### 3.3 YAML 萃取（`.yaml/.yml`）
- 同 JSON 逻辑（PyYAML 不可用时 fallback 简单行解析 `key: value`）

### 3.4 Python 萃取（`.py`）
- 仅 `__doc__`（模块 docstring·≤1000 字符）
- 顶层常量：`NAME = "str"` 形式（str 值 ≤200 字符）

### 3.5 去重与幂等
- `content_hash = sha256(content).hexdigest()[:8]`
- 同 content 已在 store → `anchors_skipped += 1` 跳过
- 重复运行 extract() → 增量（已存在锚点跳过·不重复写入）

---

## 四、依赖关系

- `CognitiveAnchorStore`（lao/effect_anchored/cognitive_anchor.py·现有·复用）
- `Anchor` dataclass（同上·现有·复用）
- **不依赖** S2-S6 任何新模块（S1 独立可测）

---

## 五、测试用例（S1 验收标准）

**测试文件:** `tests/test_runtime_extractor.py`（新建）

| # | 用例 | 断言 |
|:--|:--|:--|
| T1 | 构造临时 runtime 目录（含 SOUL.md/AGENTS.md/config.json/helper.py）→ extract | anchors_added ≥ 4·files_scanned ≥ 4 |
| T2 | SOUL.md 内容 → 锚点存在且 priority 最高(10)·tags 含 "runtime"+"md" | 锚点 value.priority == 10 |
| T3 | config.json 含 `{"agent_id":"tristan","model":"deepseek-v4-flash"}` → 两锚点 | value.content 含 tristan / deepseek-v4-flash |
| T4 | 密钥排除：config.json 含 `{"apiKey":"sk-xxx"}` → 不萃取 | 无锚点 content 含 "sk-xxx" |
| T5 | 重复 extract() 两次 → 第二次 anchors_added=0（幂等） | 第二次 anchors_added == 0 |
| T6 | 超限文件（>1MB 假文件）→ 跳过 | anchors_skipped ≥ 0·files_scanned 计数不含超限 |
| T7 | 排除目录（node_modules/xxx.md）→ 不扫描 | 无该路径锚点 |
| T8 | estimate_tokens("中文测试ABC") → 4*1 + 3*0.25 = 4.75 → int 4 | == 4 |
| T9 | 空目录/不存在路径 → fail-open 返回 ExtractionResult(全 0)不抛 | 无异常·anchors_added == 0 |
| T10 | recall_level="high_recall" 与 "high_precision" 萃取锚点数不同(recall ≥ precision) | recall >= precision |

**运行方式:** `python3 -m pytest tests/test_runtime_extractor.py -q` → 全过

---

## 六、完成定义（DoD）

- [ ] 文件创建：`lao/effect_anchored/runtime_extractor.py`（模块头注释含 `# v3.5.2-laorefactor: S1`）
- [ ] 10 个测试用例全过
- [ ] 语法检查通过（`python3 -c "import ast; ast.parse(...)"`）
- [ ] 全量回归无破坏（`python3 -m pytest tests/ -q --deselect tests/test_external_developer_journey.py::test_a_first_install_and_chat`）
- [ ] 真实 Runtime 目录（如 /home/agentuser/.openclaw/workspace/tristan）试萃取·输出 ExtractionResult 摘要
