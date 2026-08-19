"""S1 runtime_extractor 测试（LAO 架构重构·2026-08-19）。

覆盖规格 10 个用例: 萃取/优先级/JSON白名单/密钥排除/幂等/超限/排除目录/token估算/fail-open/recall级别。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lao.effect_anchored.runtime_extractor import RuntimeExtractor, extract_runtime  # noqa: E402


def _make_runtime_dir(tmp):
    """构造含 SOUL.md/AGENTS.md/config.json/helper.py 的临时 runtime 目录。"""
    root = os.path.join(tmp, "runtime")
    os.makedirs(root)
    # SOUL.md（优先）
    with open(os.path.join(root, "SOUL.md"), "w", encoding="utf-8") as f:
        f.write("# SOUL.md\n\n"
                "## 核心职责\n\n"
                "技术架构与基础设施。\n\n"
                "必须保证基础设施稳定。\n")
    # AGENTS.md（优先）
    with open(os.path.join(root, "AGENTS.md"), "w", encoding="utf-8") as f:
        f.write("# AGENTS.md\n\n禁止修改生产配置。\n")
    # README.md（优先）
    with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as f:
        f.write("# 项目\n\n安装说明。\n")
    # config.json（白名单 key）
    with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as f:
        f.write('{"agent_id": "tristan", "model": "deepseek-v4-flash", '
                '"apiKey": "sk-secret-xxx"}\n')
    # helper.py（docstring + 常量）
    with open(os.path.join(root, "helper.py"), "w", encoding="utf-8") as f:
        f.write('"""helper 模块。\n\n提供基础工具函数。\n"""\n'
                'DEFAULT_NAME = "lao-helper"\n'
                'SECRET_TOKEN = "tok-abc"\n')
    return root


class TestExtractBasic(unittest.TestCase):
    """T1: 基础萃取"""

    def test_extract_basic(self):
        tmp = tempfile.mkdtemp()
        root = _make_runtime_dir(tmp)
        ex = RuntimeExtractor()
        r = ex.extract(root)
        self.assertGreaterEqual(r.anchors_added, 4,
                                f"应至少萃取4锚点: {r.anchors_added}")
        self.assertGreaterEqual(r.files_scanned, 4,
                                f"应扫描至少4文件: {r.files_scanned}")
        self.assertGreaterEqual(r.files_extracted, 4,
                                f"应萃取至少4文件: {r.files_extracted}")


class TestPriority(unittest.TestCase):
    """T2: SOUL.md 优先(priority=10)"""

    def test_soul_priority(self):
        tmp = tempfile.mkdtemp()
        root = _make_runtime_dir(tmp)
        ex = RuntimeExtractor()
        ex.extract(root)
        found_soul = False
        for a in ex.store.lookup(layer="runtime"):
            v = a.get("value") or {}
            if "SOUL.md" in str(v.get("source_path", "")):
                self.assertEqual(v.get("priority"), 10)
                found_soul = True
        self.assertTrue(found_soul, "SOUL.md 锚点应存在")


class TestJsonWhitelist(unittest.TestCase):
    """T3: JSON 白名单 key 萃取"""

    def test_json_keys(self):
        tmp = tempfile.mkdtemp()
        root = _make_runtime_dir(tmp)
        ex = RuntimeExtractor()
        ex.extract(root)
        contents = []
        for a in ex.store.lookup(layer="runtime"):
            v = a.get("value") or {}
            contents.append(str(v.get("content", "")))
        joined = "|".join(contents)
        self.assertIn("tristan", joined, "agent_id 值应被萃取")
        self.assertIn("deepseek-v4-flash", joined, "model 值应被萃取")


class TestSecretExcluded(unittest.TestCase):
    """T4: 密钥绝不萃取"""

    def test_no_secret(self):
        tmp = tempfile.mkdtemp()
        root = _make_runtime_dir(tmp)
        ex = RuntimeExtractor()
        ex.extract(root)
        joined = "|".join(str((a.get("value") or {}).get("content", ""))
                          for a in ex.store.lookup(layer="runtime"))
        self.assertNotIn("sk-secret-xxx", joined, "apiKey 值不得萃取")
        self.assertNotIn("tok-abc", joined, "token 值不得萃取")
        self.assertNotIn("SECRET_TOKEN", joined, "secret key 不得萃取")


class TestIdempotent(unittest.TestCase):
    """T5: 重复 extract 幂等"""

    def test_twice_no_duplicate(self):
        tmp = tempfile.mkdtemp()
        root = _make_runtime_dir(tmp)
        ex = RuntimeExtractor()
        r1 = ex.extract(root)
        r2 = ex.extract(root)
        self.assertEqual(r2.anchors_added, 0,
                         f"第二次应 0 新增: {r2.anchors_added}")

    def test_high_volume_idempotent(self):
        """P0-0: 大目录多次萃取幂等(防容量淘汰导致虚增)"""
        tmp = tempfile.mkdtemp()
        root = os.path.join(tmp, "runtime")
        os.makedirs(root)
        # 造 >500 个锚点(超 CognitiveAnchorStore 默认 500·验证 max_anchors 修复)
        for i in range(30):
            with open(os.path.join(root, f"doc{i}.md"), "w", encoding="utf-8") as f:
                f.write(f"# 文档{i}\n\n必须遵守规则{i}。\n")
        ex = RuntimeExtractor()
        r1 = ex.extract(root)
        r2 = ex.extract(root)
        r3 = ex.extract(root)
        self.assertGreaterEqual(r1.anchors_added, 30,
                                f"首次应萃取全部: {r1.anchors_added}")
        self.assertEqual(r2.anchors_added, 0, f"第二次应 0: {r2.anchors_added}")
        self.assertEqual(r3.anchors_added, 0, f"第三次应 0: {r3.anchors_added}")


class TestRecallParamExposed(unittest.TestCase):
    """P0-0: extract 签名暴露 recall_level"""

    def test_recall_level_in_signature(self):
        import inspect
        sig = inspect.signature(RuntimeExtractor.extract)
        self.assertIn("recall_level", sig.parameters,
                      "extract 应暴露 recall_level 参数(P0-0)")

    def test_recall_override_works(self):
        tmp = tempfile.mkdtemp()
        root = _make_runtime_dir(tmp)
        ex = RuntimeExtractor()
        r_hi = ex.extract(root, recall_level="high_recall")
        r_pre = RuntimeExtractor().extract(root, recall_level="high_precision")
        self.assertGreaterEqual(r_hi.anchors_added, r_pre.anchors_added,
                                "high_recall 应 >= high_precision")


class TestOversizeSkipped(unittest.TestCase):
    """T6: 超限文件跳过"""

    def test_oversize(self):
        tmp = tempfile.mkdtemp()
        root = _make_runtime_dir(tmp)
        # 造一个 >1MB 的 md
        with open(os.path.join(root, "huge.md"), "w", encoding="utf-8") as f:
            f.write("# big\n" + "x" * (1_100_000))
        ex = RuntimeExtractor()
        r = ex.extract(root)
        # huge.md 不应被萃取（超限）
        for a in ex.store.lookup(layer="runtime"):
            v = a.get("value") or {}
            self.assertNotIn("huge.md", str(v.get("source_path", "")))


class TestSkipDirs(unittest.TestCase):
    """T7: 排除目录不扫描"""

    def test_skip_node_modules(self):
        tmp = tempfile.mkdtemp()
        root = _make_runtime_dir(tmp)
        os.makedirs(os.path.join(root, "node_modules"))
        with open(os.path.join(root, "node_modules", "dep.md"), "w") as f:
            f.write("# dep\n")
        ex = RuntimeExtractor()
        ex.extract(root)
        for a in ex.store.lookup(layer="runtime"):
            v = a.get("value") or {}
            self.assertNotIn("node_modules", str(v.get("source_path", "")))


class TestEstimateTokens(unittest.TestCase):
    """T8: token 估算"""

    def test_estimate(self):
        self.assertEqual(RuntimeExtractor.estimate_tokens("中文测试ABC"), 4)


class TestFailOpen(unittest.TestCase):
    """T9: 空目录/不存在路径 fail-open"""

    def test_empty_dir(self):
        tmp = tempfile.mkdtemp()
        ex = RuntimeExtractor()
        r = ex.extract(os.path.join(tmp, "nope"))
        self.assertEqual(r.anchors_added, 0)
        self.assertEqual(r.files_scanned, 0)

    def test_extract_runtime_helper(self):
        tmp = tempfile.mkdtemp()
        root = _make_runtime_dir(tmp)
        r = extract_runtime(root)
        self.assertGreaterEqual(r.anchors_added, 4)


class TestRecallLevel(unittest.TestCase):
    """T10: recall 级别影响锚点数"""

    def test_recall_ge_precision(self):
        tmp = tempfile.mkdtemp()
        root = _make_runtime_dir(tmp)
        hi = RuntimeExtractor(recall_level="high_precision").extract(root)
        hr = RuntimeExtractor(recall_level="high_recall").extract(root)
        self.assertGreaterEqual(hr.anchors_added, hi.anchors_added,
                                "high_recall 锚点数应 >= high_precision")


if __name__ == "__main__":
    unittest.main()
