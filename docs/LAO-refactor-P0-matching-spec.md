# LAO 架构重构 · P0 代码规格 · experience_matching 升级（match 引擎）

> **版本:** v1.0 | **日期:** 2026-08-19 15:00 CST
> **依据:** LAO 架构产品规格 v1.0 第三节（Shuyu·14:10）+ 创始人 13:22 施工令
> **执行:** Tristan 落盘（Qoder 沙箱写权限受限·既定模式）
> **约束:** 仅标准库 · docstring · 模块头注释 `# v3.5.2-laorefactor: P0` · fail-open · **不重写现有代码（Stella 修正 2）**

---

## 一、升级目标

在现有 `ExperienceMatcher`（`lao/effect_anchored/experience_matching.py`）上**扩展**主动匹配能力：

- **保留**：`retrieve_verified_experience()`（稳定 API·Melody 消费）+ `_query_erge` + `ExperienceHit`
- **新增**：`MatchResult` dataclass + `match()` 主动匹配引擎 + tf-idf 语义匹配 + 三段式决策（Zeus LAO-C3 双阈值灰区）

## 二、新增代码

### 2.1 MatchResult

```python
@dataclass
class MatchResult:
    """主动匹配结果（规格 3.1）。"""
    confidence: float                        # 0.0-1.0
    matched_experience: Optional[Dict[str, Any]] = None  # None=未匹配
    action: str = "proceed_to_llm"           # direct_return / flag_for_comparison / proceed_to_llm
    isolation_violation: bool = False        # 隔离键违规
    matched_via: str = "none"                # exact / semantic / none
```

### 2.2 match() 方法

```python
def match(self, request_features: Dict[str, Any]) -> MatchResult:
    """
    主动经验匹配（规格 3.2·LAO-C3 双阈值灰区）。

    输入 request_features: task_text, agent_id, task_type
    流程:
      ① 清洗 task_text + fingerprint(sha256[:16]) — 与萃取一致
      ② 精确匹配: 经验库 fingerprint == query → verified=1.0 / pending=0.6 / disputed跳过
      ③ 语义匹配: 同 agent+task_type 的 verified 经验 tf-idf cosine
      ④ 隔离键检查: matched.agent_id != request.agent_id → isolation_violation=True → proceed_to_llm
      ⑤ 三段式: confidence ≥0.85 → direct_return · 0.5-0.85 → flag_for_comparison · <0.5 → proceed_to_llm
    """
```

### 2.3 语义匹配（tf-idf cosine·标准库）

```python
def _tfidf_cosine(self, query: str, texts: List[str]) -> List[float]:
    """
    词频-逆文档频率 cosine 相似度(纯标准库·规格 3.2 ③)。

    中文按单字+双字切分(无外部分词依赖)·英文按空格。
    """
```

### 2.4 经验库读取

```python
def _load_experience_store(self) -> List[Dict[str, Any]]:
    """读 data/lao_experiences.jsonl(绝对路径·P0-2 建)。不存在返回 []。"""
```

**经验库路径:** `/home/agentuser/lao-release/lao/effect_anchored/data/lao_experiences.jsonl`

### 2.5 阈值常量

```python
HIGH_CONFIDENCE = 0.85   # 直返(LAO-C3 上阈值)
GRAY_ZONE = 0.50         # 灰区下限(0.5-0.85 → flag_for_comparison)
```

---

## 三、测试用例（P0 验收）

**测试文件:** `tests/test_experience_matching.py`（新建·不破坏现有测试）

| # | 用例 | 断言 |
|:--|:--|:--|
| M1 | 无经验库 → match | action=proceed_to_llm·confidence<0.5·matched=None |
| M2 | 精确指纹命中 verified | confidence=1.0·action=direct_return·matched_via=exact |
| M3 | 精确指纹命中 pending | confidence=0.6·action=flag_for_comparison |
| M4 | disputed 经验跳过 | 不参与匹配·action=proceed_to_llm |
| M5 | 语义匹配(相似文本) | confidence 0.5-0.85·action=flag_for_comparison |
| M6 | 隔离键违规 | isolation_violation=True·action=proceed_to_llm |
| M7 | 不同 task_type 不匹配 | 语义匹配只在同 task_type 内 |
| M8 | fail-open: 无 task_text | 返回 proceed_to_llm·不抛 |
| M9 | retrieve_verified_experience 保留 | 方法仍存在·可调用(兼容性) |
| M10 | 阈值边界 | confidence=0.85 → direct_return·0.50 → flag_for_comparison |

---

## 四、完成定义（DoD）

- [ ] `experience_matching.py` 模块头 `# v3.5.2-laorefactor: P0`（追加·不改现有 docstring）
- [ ] `ExperienceHit`/`retrieve_verified_experience` 原样保留
- [ ] 新增 `MatchResult` + `match()` + `_tfidf_cosine` + `_load_experience_store`
- [ ] 10 测试全过
- [ ] 全量回归无破坏（338+ 现有测试）
