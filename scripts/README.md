# Scripts 目录说明

## 生产环境脚本

### `init_production.py` ⭐ 推荐
生产环境一键初始化脚本，智能检测并创建索引。

**使用场景**：
- 新环境部署
- 数据入库后创建索引
- 定期检查并补全缺失索引

**执行**：
```bash
export PGVECTOR_URL="postgres://user:pass@host:5432/dbname"
python scripts/init_production.py
```

### `create_vector_index.py`
单独创建向量索引（IVFFLAT），需要数据量≥100条。

**使用场景**：
- 仅需创建/重建向量索引
- init_production.py 之外的独立操作

**执行**：
```bash
python scripts/create_vector_index.py
```

---

## 开发/测试脚本

### `generate_test_data.py`
生成2000条测试数据用于开发和性能测试。

**执行**：
```bash
python scripts/generate_test_data.py
```

### `benchmark_retrieval.py`
性能基准测试，验证索引效果与检索速度。

**执行**：
```bash
python scripts/benchmark_retrieval.py
```

### `run-all-tests.sh`
运行所有集成测试。

**执行**：
```bash
./scripts/run-all-tests.sh
```

---

## 废弃脚本（已删除）

- `_check_vector.py` - 临时调试脚本
- `_fix_vector_dimension.py` - 一次性修复脚本
- `apply_pgvector_indexes.py` - 功能已被 init_production.py 取代

---

## SQL 文件

### `sql/pgvector_indexes.sql`
索引定义 SQL（仅作参考，推荐使用 Python 脚本执行）。

包含：
- 向量索引（IVFFLAT，已注释）
- 元数据索引（platform/date/score）
- ANALYZE 统计更新
