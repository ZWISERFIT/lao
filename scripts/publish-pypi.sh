#!/bin/bash
# LAO PyPI 发布脚本
# 用途: 将 lineage-anchored-ontology 发布到 PyPI
# DRI: Zeus | 所属: ZWISERFIT 第一个对外开源产品

set -e

REPO_DIR="/home/agentuser/.openclaw/workspace/lineage-anchored-ontology"
cd "$REPO_DIR"

echo "=== LAO PyPI 发布流程 ==="
echo ""

# Step 1: 测试通过
echo ">>> Step 1: 运行全部测试"
source .venv/bin/activate
python -m pytest tests/ -q
echo "✅ 测试全部通过"
echo ""

# Step 2: 检查版本号
VERSION=$(python -c "from effect_anchored import __version__; print(__version__)" 2>/dev/null || echo "未找到__version__")
echo ">>> Step 2: 当前版本: $VERSION"
echo ""

# Step 3: 清理旧构建
echo ">>> Step 3: 清理旧构建产物"
rm -rf dist/ build/ *.egg-info/ effect_anchored_ontology.egg-info/
echo "✅ 清理完成"
echo ""

# Step 4: 构建
echo ">>> Step 4: 构建 wheel + sdist"
python -m build
echo "✅ 构建完成"
ls -la dist/
echo ""

# Step 5: twine check
echo ">>> Step 5: twine 验证"
python -m twine check dist/*
echo "✅ 验证通过"
echo ""

# Step 6: 上传到 TestPyPI（先测试）
echo ">>> Step 6: 上传到 TestPyPI（测试环境）"
echo "如果还未准备Token，跳过此步"
# python -m twine upload --repository testpypi dist/*
echo "⏭️ 跳过（需要PyPI token）"
echo ""

# Step 7: 正式发布指令
echo "========================================="
echo "   发布就绪！手动执行正式发布："
echo ""
echo "   python -m twine upload dist/*"
echo ""
echo "   或先测试："
echo "   python -m twine upload --repository testpypi dist/*"
echo "   pip install --index-url https://test.pypi.org/simple/ lineage-anchored-ontology"
echo ""
echo "   正式安装："
echo "   pip install lineage-anchored-ontology"
echo "========================================="
