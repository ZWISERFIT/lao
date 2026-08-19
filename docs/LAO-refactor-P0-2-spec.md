# LAO 架构重构 · P0-2 代码规格 · experience_extractor + retry_counter

> **版本:** v1.0 | **日期:** 2026-08-19 14:30 CST
> **依据:** LAO 架构产品规格 v1.0（Shuyu·2026-08-19 14:10）+ 创始人 13:22 施工令
> **执行:** Qoder（GLM-5.3）→ Tristan 落盘验收
> **约束:** 仅标准库 · docstring · 模块头注释 `# v3.5.2-laorefactor: P0-2` · fail-open · 不修改现有文件

---

## 一、模块 1: experience_extractor.py

**模块路径:** `lao/effect_anchored/experience_extractor.py`（新建）

**职责:** 回站验收后·每次 LLM 交互结果 → 萃取经验 → 存入经验库（JSONL）。

### 1.1 数据类

```python
@dataclass
class ExperienceRecord:
    """一次完整 LLM 交互的经验记录(规格 2.1)。"""
    experience_id: str              # exp-{fingerprint}
    experience_fingerprint: str     # sha256(cleaned_task_text)[:16]
    experience_content: str         # "Q: ...\nA: ..." (≤500 chars)
    task_type: str                  # fact/decision/cognitive/chat
    agent_id: str                   # 隔离键
    model_used: str
    provider_used: str
    quality_grade: str              # verified/pending/disputed
    cache_hit: bool
    actual_cost: float
    context_tokens: int
    response_tokens: int
    retry_count: int
    isolation_key: str              # = agent_id
    created_at: str                 # ISO 时间(含时区)
    verified_by: str = "lao_acceptance"

    def to_jsonl(self) -> str: ...
    @classmethod
    def from_dict(cls, d: dict) -> "ExperienceRecord": ...
```

### 1.2 主类

```python
class ExperienceExtractor:
    """经验萃取器: LLM交互记录 → 经验库(JSONL)。"""

    STORE_PATH = "/home/agentuser/lao-release/lao/effect_anchored/data/lao_experiences.jsonl"
    MAX_CONTENT_CHARS = 500
    SECRET_RE = re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*\S|sk-[A-Za-z0-9_\-]{6,}")

    def __init__(self, store_path: str = STORE_PATH):
        # 确保 data/ 目录存在(Stella修正3: 绝对路径·先建data目录)
        ...

    def extract(self, record: dict) -> str | None:
        """萃取一条经验 → 存入经验库。返回 experience_id·失败返回 None。

        输入 record 字段(规格2.1): request_id/request_features/response_features/verification
        """
        ...

    def _clean_task_text(self, text: str) -> str:
        """清洗: 去无关上下文·保留核心问题·去密钥特征。"""
        ...

    def _extract_fingerprint(self, cleaned: str) -> str:
        """sha256(cleaned)[:16]"""
        ...

    def _grade(self, verification: dict) -> str:
        """质量评级: fact_verified=True+cognitive_consistent=True+retry=0 → verified
        fact_verified=True+retry>0 → pending · fact_verified=False → disputed"""
        ...

    def _build_content(self, cleaned_q: str, response_text: str) -> str:
        """精简: "Q: {cleaned}\nA: {核心结论}"·首句结论+关键事实·≤500 chars"""
        ...

    def _looks_secret(self, text: str) -> bool: ...

    def save(self, rec: ExperienceRecord) -> None:
        """追加 JSONL。同 fingerprint+agent_id → 覆盖(去重·规格2.4)。"""
        ...

    def load_all(self) -> List[dict]:
        """读取全部经验(供匹配引擎)。"""
        ...

    def dedupe(self) -> int:
        """重写文件: 同 fingerprint+agent_id 只保留最新。返回删除数。"""
        ...
```

### 1.3 关键规则（规格 2.4）

| 规则 | 实现 |
|:--|:--|
| 不萃取密钥 | `_clean_task_text`/`_build_content` 前调 `_looks_secret`·命中返回空 |
| 不萃取 PII | 不做复杂检测·至少去除密钥特征 |
| 限长 | experience_content ≤ 500 chars（截断） |
| 去重 | 同 fingerprint+agent_id 只留最新（save 时覆盖+dedupe 重写） |
| disputed 也存 | 存但 quality_grade=disputed·匹配引擎跳过 |

---

## 二、模块 2: retry_counter.py

**模块路径:** `lao/effect_anchored/retry_counter.py`（新建）

**职责:** 退回重做计数器（进程内存·request_id → count·上限 3）。

```python
class RetryCounter:
    """退回重做计数器(进程内存·不持久化·规格1.3)。

    生命周期: 请求到达初始化为0 · 每次退回+1 · 交付后清除。
    上限: 3次·第3次后调用方降级Flash交付。
    """

    MAX_RETRIES = 3

    def __init__(self):
        self._counts: Dict[str, int] = {}

    def init(self, request_id: str) -> int:
        """初始化计数器为0·返回0。"""
        ...

    def increment(self, request_id: str) -> int:
        """退回+1·返回当前计数(超上限返回MAX_RETRIES)。"""
        ...

    def get(self, request_id: str) -> int: ...
    def should_retry(self, request_id: str) -> bool:
        """get < MAX_RETRIES → True(可重试)。"""
        ...
    def clear(self, request_id: str) -> None:
        """交付后清除。"""
        ...
    def summary(self) -> Dict[str, int]:
        """{active_requests: n, total_retries: n}"""
        ...
```

---

## 三、data 目录

**路径:** `/home/agentuser/lao-release/lao/effect_anchored/data/`
- `ExperienceExtractor.__init__` 自动 `os.makedirs(data_dir, exist_ok=True)`
- `lao_experiences.jsonl` 首次写入时创建（含 header 注释行可选·不强制）

---

## 四、测试用例（P0-2 验收）

**测试文件:** `tests/test_experience_extractor.py`（新建）

| # | 用例 | 断言 |
|:--|:--|:--|
| T1 | 构造 verified record → extract | 返回 exp-{fp}·文件存在·1 行 |
| T2 | 同 fingerprint+agent_id 二次 extract | 覆盖·文件仍 1 行·内容为最新 |
| T3 | 密钥内容请求 → extract | 返回 None·不写入(不萃取密钥) |
| T4 | content > 500 chars 响应 | experience_content ≤ 500 |
| T5 | verification 各种组合 → quality_grade 正确 | verified/pending/disputed |
| T6 | fingerprint = sha256[:16] 长度 16 | == 16 |
| T7 | 隔离键 isolation_key == agent_id | 一致 |
| T8 | load_all 返回全部记录 | count == 文件行数 |
| T9 | dedupe 去重 | 构造 2 条同 fp 不同内容 → dedupe 删 1 |
| T10 | fail-open: 无效 record(dict 缺字段) | 返回 None·不抛 |

**测试文件:** `tests/test_retry_counter.py`（新建）

| # | 用例 | 断言 |
|:--|:--|:--|
| R1 | init → get == 0 | == 0 |
| R2 | increment 3 次 → get == 3 | == 3 |
| R3 | should_retry: 0/1/2 → True·3 → False | 符合 |
| R4 | clear 后 get == 0·summary active 减 1 | 符合 |
| R5 | summary 统计 | active_requests/total_retries 正确 |
| R6 | 上限: increment 5 次 → get == 3(不超) | == 3 |

---

## 五、完成定义（DoD）

- [ ] `lao/effect_anchored/experience_extractor.py`（模块头 `# v3.5.2-laorefactor: P0-2`）
- [ ] `lao/effect_anchored/retry_counter.py`（同上）
- [ ] `lao/effect_anchored/data/` 目录 + `lao_experiences.jsonl` 自动创建
- [ ] 16 个测试全过（10 extractor + 6 counter）
- [ ] 语法检查通过
- [ ] 全量回归无破坏（317+ 现有测试）
