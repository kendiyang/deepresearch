#!/usr/bin/env bash
set -euo pipefail

# 一键运行本仓库的两套集成测试
# 1) 数据清洗（DataRefinery）
# 2) 存储与检索（PGVector via langchain_postgres）

# 进入脚本所在项目根（允许从任意位置调用）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# 选择 Python 解释器（优先使用本项目 venv）
PY=".venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="python"
fi

echo "[1/2] 运行数据清洗集成测试..."
"$PY" test_data_refinery_integration.py

echo "[2/2] 运行存储与检索集成测试..."
# 默认数据库连接串（可通过外部环境变量覆盖）
: "${PGVECTOR_URL:=postgresql+psycopg2://tenmuses:tenmuses_dev@localhost:5432/tenmuses}"
PGVECTOR_URL="$PGVECTOR_URL" "$PY" test_storage_and_retrieval_integration.py

echo "\n✅ 所有集成测试已完成"
