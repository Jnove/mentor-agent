#!/usr/bin/env bash
# 拉取私有知识库（正式库 + 待审查 staging）到 knowledge_base/。
# 主仓库 .gitignore 已忽略该目录，所以代码仓保持公开、知识库保持私有。
# 用法: bash scripts/fetch_kb.sh   （Git Bash 里跑，Windows 兼容）
set -euo pipefail

KB_REPO="${KB_REPO:-https://github.com/Jnove/mentor-agent-kb.git}"

if [ -d "knowledge_base/.git" ]; then
    echo "knowledge_base 已是 git 仓库，执行 pull 更新..."
    git -C knowledge_base pull
elif [ -e "knowledge_base" ]; then
    echo "knowledge_base 目录已存在但不是 git 仓库，请先移走/删除后重试。"
    exit 1
else
    git clone "$KB_REPO" knowledge_base
    echo "知识库已拉到 knowledge_base/（正式库 + staging/ 待审查区）。"
fi
