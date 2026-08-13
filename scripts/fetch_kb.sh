#!/usr/bin/env bash
# 按主仓库固定的 commit 初始化/同步知识库子模块。
# 用法: bash scripts/fetch_kb.sh   （Git Bash 里跑，Windows 兼容）
set -euo pipefail

git submodule sync -- knowledge_base
git submodule update --init --recursive --checkout knowledge_base
printf 'knowledge_base commit: '
git -C knowledge_base rev-parse HEAD
