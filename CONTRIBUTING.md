# Contributing to LAO

> **LAO 的信任哲学：** 信任不是营销出来的——是透明的、可验证的。每一条 Issue、每一次修复、每一次讨论，都是信任构建的证据。你的每一次贡献都会被记录（见 [TRUST-BUILDERS.md](./TRUST-BUILDERS.md)）。

谢谢你有兴趣让 LAO 更好。这份文档会带你从零开始，成为早期 Trust Builder。

---

## 目录

- [快速开始](#快速开始)
- [搭建开发环境](#搭建开发环境)
- [从哪里开始：easy-first-issue](#从哪里开始easy-first-issue)
- [提交一个 PR](#提交一个-pr)
- [报告 Bug 或提想法](#报告-bug-或提想法)
- [我们的贡献原则](#我们的贡献原则)
- [行为准则](#行为准则)

---

## 快速开始

```bash
# 1. 安装（本地开发用 editable 模式）
pip install -e .
pip install pytest   # 当前 dev 依赖用单独安装（见 pyproject：零运行时依赖）

# 2. 跑通测试
pytest

# 3. 试一下 LAOAgent
python -c "from lao import LAOAgent; ai = LAOAgent(); print(ai.predict('member_0421', '下周会来训练'))"
```

LAO 是一个零配置、零外部依赖的库。装完就能跑。

---

## 搭建开发环境

```bash
git clone https://github.com/ZWISERFIT/lao.git
cd lao
python -m venv .venv && source .venv/bin/activate
pip install -e .
pip install pytest
pytest   # 应全部通过（clone 后首次）
```

**运行测试要求：** 提交前请确保 `pytest` 全绿。我们不做「应该能过」——做「实测全过」。

---

## 从哪里开始：easy-first-issue

我们不希望你面对空仓库无从下手。所以在 Issues 里维护了一批 **`good first issue`** 标记的低门槛任务：

- 都是**独立小任务**，不依赖你不知道的上下文
- 每个 Issue 都包含：问题描述 → 技术上下文链接 → 预期改动范围 → **你如何验证自己的改动**
- 难度适合第一次接触代码库

**去找它们：** 打开 [Issues](https://github.com/ZWISERFIT/lao/issues) → 点 `good first issue` 标签筛选。

**认领一个：** 在 Issue 下留言 `@ZWISERFIT/luna 我来认领这个`，我们会把它标记为「已认领」避免撞车。24 小时内会有人响应你。

**如果你卡住了：** 直接在那个 Issue 下提问，不要闷头硬撑。社区的意义就是一起把问题解决。

---

## 提交一个 PR

```bash
# 1. fork 仓库并 clone 到本地
# 2. 建一个描述性分支
git checkout -b fix/describe-what-you-fix

# 3. 改代码 + 加/改测试
# 4. 本地全量测试
pytest

# 5. 提交 + push
git add .
git commit -m "fix: 描述你修复了什么"
git push origin fix/describe-what-you-fix

# 6. 在 GitHub 开一个 PR，标题写清楚，描述里关联对应 Issue（如 Fixes #12）
```

**PR 合并标准（我们很认真，因为这是信任的体现）：**
- ✅ `pytest` 全绿
- ✅ 有对应测试（能证明你的改动有效）
- ✅ 数字/声明有来源（见 [我们的贡献原则](#我们的贡献原则)）
- ✅ 不引入不必要的大改动（小而清晰的 PR 更容易被合并）

合并后，你会被加进 [TRUST-BUILDERS.md](./TRUST-BUILDERS.md)——你的名字会被记住。

---

## 报告 Bug 或提想法

**Bug：** 开一个 [Issue](https://github.com/ZWISERFIT/lao/issues/new)，模板会引导你填：
- 复现步骤
- 期望行为 vs 实际行为
- 环境（Python 版本、平台）

**想法/功能：** 也可以开 Issue，标题用 `[idea]` 前缀。我们欢迎讨论，但会诚实地告诉你「做还是不做、为什么」——不会为了显得热情而承诺一堆做不到的事。

**我们的承诺：** 每个 Issue 都会得到**透明回复**（是/否/以及原因），关闭率目标 ≤ 48 小时。GitHub Issues 是公开的——每一次交流都是信任构建的证据。

---

## 我们的贡献原则

1. **不虚构数据。** 任何数字、声明、性能指标，都要有来源（代码、测试、真实运行日志）。当前装机量和外部贡献者都是 0——我们诚实写「刚刚开源」，不包装。
2. **LAO 自身 = 第一案例。** 我们自己的 Agent 集群就在 LAO 校验下运行（吃自己的狗粮）。你修复的每个 Bug，都是对「错误 → 修复 → 不再犯」这个循环的实践。
3. **不做比较攻击。** 不说「比 X 更好」。我们说「我们解决的是不同的问题」——LAO 是确定性行为校验，不是概率规则过滤。
4. **小步、透明。** 宁可小而清晰的 PR，不要大而含混的改动。

---

## 行为准则

- **善意假设。** 你面对的是真实的人，可能有不同的时区、语言和背景。
- **高效沟通。** 中文或英文都可以，说清楚比说得漂亮重要。
- **不开玩笑地遵守上述原则。** 信任是这个项目的一切，而信任从每一次透明、可验证的交互开始。

---

*LAO — 让 Agent 像人一样靠谱。不再忘事，不再胡说。*
