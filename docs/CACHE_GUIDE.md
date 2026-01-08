# 缓存层使用指南

## 概述

为了控制 Serper API 成本（$1/1000次），系统集成了双模式缓存层，可以在重复搜索时显著降低 API 调用次数。

## 快速开始

### 默认配置（推荐）

```python
from services.discovery.discovery import DiscoveryService

# 自动模式：优先Redis，失败则降级到文件缓存
discovery = DiscoveryService(enable_cache=True)
```

### Redis 模式（生产环境）

```python
# 方式1：环境变量
export REDIS_URL="redis://localhost:6379/0"

# 方式2：代码配置
discovery = DiscoveryService(
    enable_cache=True,
    cache_type='redis',
)
```

### 文件模式（开发/测试）

```python
# 方式1：环境变量
export CACHE_DIR="./cache/serper"

# 方式2：代码配置
discovery = DiscoveryService(
    enable_cache=True,
    cache_type='file',
)
```

### 禁用缓存

```python
# 测试场景或需要实时数据
discovery = DiscoveryService(enable_cache=False)
```

## 缓存策略

### 缓存键生成

```python
# Key: md5(dork_string)
dork = "site:reddit.com skincare trends 2026"
cache_key = "serper:dork:" + md5(dork).hexdigest()
# => serper:dork:a1b2c3d4e5f6...
```

### 缓存值

完整的 Serper API JSON 响应：

```json
{
  "organic": [
    {
      "link": "https://www.reddit.com/r/beauty/...",
      "title": "Skincare Trends 2026",
      "position": 1
    }
  ],
  "searchParameters": {...}
}
```

### TTL（过期时间）

- **默认**：86400秒（24小时）
- **理由**：搜索结果的时效性通常为日级
- **自定义**：

```python
discovery = DiscoveryService(
    enable_cache=True,
    cache_ttl=3600,  # 1小时
)
```

## 成本收益

### 真实测试数据

基于固定3个Dorks的测试：

| 指标 | 第一次运行（冷启动） | 第二次运行（缓存命中） |
|------|-------------------|---------------------|
| API调用 | 3次 | 0次 |
| 耗时 | 1.92秒 | 0.003秒 |
| 成本 | $0.003 | $0 |
| 加速比 | 1x | **626x** |

### 月度成本分析（假设场景）

假设条件：
- 每日10个topic
- 每topic生成10个dorks
- 月总请求数：10 × 10 × 30 = 3000次
- 缓存命中率：70%（考虑重复topic）

| 项目 | 无缓存 | 有缓存 | 节省 |
|------|--------|--------|------|
| API调用数 | 3000次 | 900次 | 2100次 |
| 月成本 | $3.00 | $0.90 | **$2.10 (70%)** |
| 年成本 | $36.00 | $10.80 | **$25.20** |

## 监控与统计

### 实时统计

```python
discovery = DiscoveryService(enable_cache=True)
results = discovery.find_discussion_urls(dork_result)

# 获取缓存统计
stats = discovery.metrics.get_stats()
print(stats)
# {
#   'total_requests': 10,
#   'cache_hits': 7,
#   'cache_misses': 3,
#   'hit_rate_percent': 70.0,
#   'api_calls': 3,
#   'cost_saved_usd': 0.007,
#   'estimated_cost_usd': 0.003
# }
```

### 缓存层统计

```python
from services.discovery.cache import SearchCache

cache = SearchCache(cache_type='file')
stats = cache.stats()
print(stats)
# {
#   'cache_type': 'file',
#   'total_files': 23,
#   'total_size_mb': 0.08,
#   'cache_dir': '/path/to/cache/serper',
#   'ttl_seconds': 86400
# }
```

### 日志输出

```
INFO: 缓存层已启用: file
INFO: [缓存命中] Dork: site:reddit.com skincare trends | hits=10
INFO: [API调用] Dork 搜索完成: hits=10 url=site:tiktok.com...
INFO: 汇总后有效链接: 89
INFO: 缓存统计: 命中率=70.0% | API调用=3次 | 节省成本=$0.0070
```

## 维护操作

### 清空缓存

```python
from services.discovery.cache import SearchCache

cache = SearchCache(cache_type='file')
cache.clear()  # 删除所有缓存文件
print("✓ 缓存已清空")
```

### Redis 缓存管理

```bash
# 查看所有缓存键
redis-cli --scan --pattern "serper:dork:*"

# 清空所有缓存
redis-cli --scan --pattern "serper:dork:*" | xargs redis-cli DEL

# 查看缓存大小
redis-cli --scan --pattern "serper:dork:*" | wc -l
```

### 文件缓存清理

```bash
# 查看缓存目录
ls -lh ./cache/serper/

# 查看缓存总大小
du -sh ./cache/serper/

# 删除过期缓存（24小时以上）
find ./cache/serper -name "*.json" -mtime +1 -delete

# 清空所有缓存
rm -rf ./cache/serper/*.json
```

## 最佳实践

### 1. 生产环境使用 Redis

- ✅ **多实例共享**：所有服务器共享缓存，提高命中率
- ✅ **自动过期**：Redis原生支持TTL，无需手动清理
- ✅ **性能优越**：内存读取比文件快10+倍

```python
# 生产配置示例
discovery = DiscoveryService(
    enable_cache=True,
    cache_type='redis',
    cache_ttl=86400,  # 24小时
)
```

### 2. 开发环境使用文件缓存

- ✅ **无需外部依赖**：不需要安装Redis
- ✅ **易于调试**：可直接查看 `.json` 文件内容
- ✅ **自动降级**：Redis失败时的备选方案

```python
# 开发配置示例
discovery = DiscoveryService(
    enable_cache=True,
    cache_type='file',
)
```

### 3. 监控缓存命中率

定期检查缓存命中率，优化搜索策略：

- **命中率 < 30%**：topic太分散，考虑调整研究方向
- **命中率 30-60%**：正常水平
- **命中率 > 60%**：优秀，缓存收益显著

### 4. 合理设置 TTL

不同场景的推荐TTL：

| 场景 | TTL | 说明 |
|------|-----|------|
| 历史数据研究 | 7天 | 数据不变，可延长缓存 |
| 市场趋势分析 | 24小时 | 平衡时效性和成本 |
| 实时监控 | 1小时 | 需要最新数据 |
| 测试环境 | 禁用缓存 | 确保测试准确性 |

## 故障排除

### 问题1：Redis连接失败

```
ERROR: Redis 初始化失败，降级到文件缓存: Connection refused
```

**解决方案**：
1. 检查Redis是否运行：`redis-cli ping`
2. 检查环境变量：`echo $REDIS_URL`
3. 系统会自动降级到文件缓存，不影响功能

### 问题2：缓存目录权限不足

```
ERROR: 缓存写入失败: Permission denied
```

**解决方案**：
```bash
# 创建缓存目录并赋权
mkdir -p ./cache/serper
chmod 755 ./cache/serper
```

### 问题3：缓存未命中预期

```
INFO: 缓存统计: 命中率=0.0%
```

**原因分析**：
- LLM每次生成的Dorks可能略有不同
- 新的research topic没有历史缓存

**建议**：
- 使用固定的Dorks进行测试
- 等待系统运行一段时间积累缓存

### 问题4：缓存占用磁盘过大

```bash
$ du -sh ./cache/serper
512M    ./cache/serper
```

**解决方案**：
```bash
# 删除超过7天的旧缓存
find ./cache/serper -name "*.json" -mtime +7 -delete

# 或设置自动清理定时任务
echo "0 2 * * * find /path/to/cache/serper -mtime +7 -delete" | crontab -
```

## 测试脚本

运行完整的缓存测试套件：

```bash
python scripts/test_cache_integration.py
```

测试覆盖：
- ✅ 缓存层基础功能（读写、过期）
- ✅ DiscoveryService集成测试
- ✅ 成本对比分析
- ✅ 缓存过期机制验证

预期输出：
```
======================================================================
  📊 测试总结
======================================================================
  总测试数: 4
  通过: 4 ✅
  失败: 0 ❌

🎉 所有测试通过！缓存层已就绪，可用于生产环境。
```

## 技术细节

### 架构设计

```
DiscoveryService
    ↓
SearchCache (cache.py)
    ↓
┌─────────────────┐
│  Redis 模式      │  ← 生产环境推荐
│  - 分布式共享    │
│  - 自动TTL      │
└─────────────────┘
    或
┌─────────────────┐
│  File 模式       │  ← 开发/降级
│  - 本地JSON     │
│  - 手动过期检查 │
└─────────────────┘
```

### 性能特性

| 特性 | Redis | 文件 |
|------|-------|------|
| 读取延迟 | ~1ms | ~10ms |
| 写入延迟 | ~1ms | ~20ms |
| 并发支持 | ✅ 优秀 | ⚠️ 一般 |
| TTL自动清理 | ✅ 原生支持 | ❌ 需手动检查 |
| 多实例共享 | ✅ 支持 | ❌ 不支持 |
| 适用场景 | 生产环境 | 开发/测试 |

### 依赖要求

**Redis 模式**：
```bash
pip install redis
# 或者已在 requirements.txt 中
```

**文件模式**：
- 无额外依赖
- 需要文件系统写权限

## 总结

缓存层的核心价值：

1. **💰 成本节省**：70%命中率可节省70% API费用
2. **⚡ 性能提升**：缓存命中时速度提升600+倍
3. **🛡️ 生产就绪**：自动降级、完整监控、易于维护
4. **🎯 灵活配置**：支持多种模式，适应不同场景

建议在生产环境启用缓存，定期监控命中率和成本节省情况！
