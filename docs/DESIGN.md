# Deep Research 系统 - 详细设计文档

## 目录
1. [系统概述](#系统概述)
2. [架构设计](#架构设计)
3. [核心模块](#核心模块)
4. [Dorks生成改进](#dorks生成改进)
5. [数据存储](#数据存储)
6. [检索系统](#检索系统)
7. [生产部署](#生产部署)

---

## 系统概述

### 项目目标
Deep Research 是一个多源信息聚合和智能检索系统，用于：
- 🔍 从互联网多个渠道自动收集研究相关数据
- 🧠 通过LLM智能化生成搜索策略（Dorks）
- 📊 向量化存储和语义检索
- 🎯 支持多维度过滤和采样

### 核心特性
- **多源覆盖**：Reddit、TikTok、Instagram、YouTube、博客、新闻等
- **智能Dorks生成**：基于LLM的领域识别和关键词优化
- **向量搜索**：使用pgvector实现高效的语义相似性检索
- **元数据过滤**：支持平台、时间、评分等多维过滤
- **生产就绪**：完整的部署、初始化、索引管理方案

---

## 架构设计

### 系统架构图

```
┌─────────────────────────────────────────────────────────────┐
│                    用户输入（研究话题）                       │
└────────────────────┬────────────────────────────────────────┘
                     │
        ┌────────────▼─────────────┐
        │  EnterpriseDorkGenerator  │
        │  (LLM驱动的搜索策略生成)  │
        └────────────┬─────────────┘
                     │
    ┌────────────────┼────────────────┐
    │ Phase 1: 战略规划              │
    │ - 领域识别 (B2C/B2B/Tech/Med)   │
    │ - 关键词英文翻译               │
    │ - 时间窗口计算                 │
    └────────────────┼────────────────┘
                     │
    ┌────────────────┼────────────────┐
    │ Phase 2: 战术执行              │
    │ - 多源覆盖规则应用             │
    │ - 10个多样化Dorks生成          │
    │ - 关键词角度多样化             │
    └────────────────┼────────────────┘
                     │
        ┌────────────▼─────────────┐
        │   DiscoveryService       │
        │  (并发搜索执行)          │
        └────────────┬─────────────┘
                     │
    ┌────────────────┼────────────────┐
    │ 搜索执行流程                    │
    │ - Serper API调用               │
    │ - 结果验证和规范化             │
    │ - URL去重                      │
    └────────────────┼────────────────┘
                     │
        ┌────────────▼──────────────┐
        │  storageAndRetrieval      │
        │  (向量存储和检索)         │
        └────────────┬──────────────┘
                     │
    ┌────────────────┼────────────────┐
    │ PostgreSQL + pgvector          │
    │ - 原始文档存储                 │
    │ - 向量嵌入索引                 │
    │ - 元数据索引                   │
    └────────────────┼────────────────┘
                     │
        ┌────────────▼──────────────┐
        │  AdvancedRetriever        │
        │  (智能检索)               │
        └────────────┬──────────────┘
                     │
    ┌────────────────┼────────────────┐
    │ 检索特性                       │
    │ - 向量相似性搜索              │
    │ - 元数据过滤                  │
    │ - 语义聚类采样                │
    │ - 多维度排序                  │
    └────────────────────────────────┘
```

### 四层架构

| 层级 | 组件 | 职责 |
|------|------|------|
| **应用层** | EnterpriseDorkGenerator, DiscoveryService | 搜索策略生成、多源数据收集 |
| **服务层** | AdvancedRetriever, storageAndRetrieval | 向量化、存储、检索 |
| **数据层** | PostgreSQL + pgvector | 持久化存储、向量索引 |
| **基础层** | LLM API, Serper API | 外部智能服务 |

---

## 核心模块

### 1. EnterpriseDorkGenerator（搜索策略生成）

**位置**：`services/discovery/discovery.py`

**功能**：基于LLM将用户研究话题转换为高效的Google Dorks

**核心方法**：
```python
class EnterpriseDorkGenerator:
    def run(self, topic: str) -> DorkResult:
        """
        输入：研究话题
        输出：10个多源覆盖的Google Dorks列表
        """
```

**两阶段流程**：

**Phase 1：战略规划（SearchStrategy）**
- 领域识别：识别话题属于 B2C_CONSUMER / B2B_ENTERPRISE / TECHNICAL_DEV / ACADEMIC_MEDICAL
- 关键词翻译：将中文话题翻译为英文关键词（3个）
- 平台选择：推荐目标平台
- 时间窗口：计算搜索的时间范围

**Phase 2：战术执行（DorkResult）**
- 调用 `_get_domain_sources()` 获取多元化源配置
- 根据领域生成10个Dorks，覆盖多个平台和关键词角度
- 避免过度限制（如不用 `site:reddit.com/r/SkincareAddiction`，而用 `site:reddit.com`）

**多源覆盖配置**（`_get_domain_sources()`）：

```python
{
  "B2C_CONSUMER": {
    "social_media": ["site:reddit.com", "site:tiktok.com", "site:instagram.com", "site:youtube.com"],
    "commerce": ["site:amazon.com/reviews", "site:trustpilot.com", "site:g2.com"],
    "media": ["site:voguebusiness.com", "site:vogue.com", "site:forbes.com", ...],
    "blogs": ["site:medium.com", "site:substack.com", ...]
  },
  "B2B_ENTERPRISE": { ... },
  "TECHNICAL_DEV": { ... },
  "ACADEMIC_MEDICAL": { ... }
}
```

**关键词角度策略**：
- 趋势和预测：trends, forecast, 2026, upcoming
- 病毒和热点：viral, trending, buzz
- 批评和问题：issue, fail, problem, controversy
- 商业角度：strategy, marketing, ROI
- 消费评价：review, feedback, worth it, overrated

### 2. DiscoveryService（多源搜索执行）

**位置**：`services/discovery/discovery.py`

**功能**：执行Dorks搜索，获取多源链接

**核心方法**：
```python
class DiscoveryService:
    def find_discussion_urls(
        self, 
        dork_result: DorkResult, 
        *, 
        per_dork: int = 20
    ) -> List[DiscoveryItem]:
        """
        输入：Dorks列表
        输出：去重后的DiscoveryItem列表
        """
```

**执行流程**：
1. **并发搜索**：使用ThreadPoolExecutor并发执行10个Dorks搜索
2. **结果验证**：每个URL通过 `_validate_link()` 验证
   - Reddit：只允许 `/r/` 路径下的内容，排除系统页面
   - TikTok/YouTube：只允许视频内容
   - 其他：默认放行
3. **URL规范化**：`_normalize_url()` 统一URL格式
4. **去重**：相同URL只保留一次

**输出结构**：
```python
class DiscoveryItem(BaseModel):
    url: str           # 发现的URL
    domain: str        # 域名
    dork: str          # 来自哪个Dork
    rank: int          # 在Serper结果中的排名
    title: Optional[str]  # 页面标题
```

**成本控制与缓存机制**：

为了控制 Serper API 成本（$1/1000次），DiscoveryService 集成了双模式缓存层：

```python
class DiscoveryService:
    def __init__(
        self,
        *,
        enable_cache: bool = True,      # 启用缓存
        cache_type: Optional[str] = None,  # "redis" | "file" | None (自动)
        cache_ttl: int = 86400,         # 缓存TTL（24小时）
    ):
        ...
```

**缓存策略**：
- **Key生成**：`md5(dork_string)` 确保同一Dork命中缓存
- **Value存储**：完整的 Serper API JSON 响应
- **TTL设置**：默认24小时（搜索结果日级时效性）
- **缓存模式**：
  - **Redis模式**（生产推荐）：支持分布式共享，多实例共享缓存
  - **文件模式**（开发/降级）：本地 `.json` 文件缓存
  - **自动模式**：优先 Redis，失败则降级到文件缓存

**成本收益**（基于真实测试）：
- **命中率**：相同 topic 重复搜索可达 100% 缓存命中
- **性能提升**：缓存命中时速度提升 600+ 倍（API调用 1.9秒 → 缓存读取 0.003秒）
- **成本节省**：假设 70% 命中率，月节省 $2.10/月（年节省 $25.20）

**监控指标**：
```python
# 查看缓存统计
stats = discovery.metrics.get_stats()
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

**使用示例**：
```python
# 开发环境：使用文件缓存
discovery = DiscoveryService(
    enable_cache=True,
    cache_type='file',
)

# 生产环境：使用 Redis
discovery = DiscoveryService(
    enable_cache=True,
    cache_type='redis',
    cache_ttl=86400,
)
# 环境变量：REDIS_URL=redis://localhost:6379/0

# 禁用缓存（测试场景）
discovery = DiscoveryService(enable_cache=False)
```

### 3. storageAndRetrieval（向量存储）

**位置**：`services/knowledge/storageAndRetrieval.py`

**功能**：管理向量数据库的存储、索引、检索

**核心特性**：
- 使用 pgvector 存储和索引文本向量
- 支持元数据过滤（platform, date, score）
- 批量插入优化
- 自动嵌入向量

**主要方法**：
- `insert_documents(docs)` - 批量插入文档
- `retrieve_similar(query, k)` - 向量相似性搜索
- `filter_by_metadata(filters)` - 元数据过滤

### 4. AdvancedRetriever（智能检索）

**位置**：`services/knowledge/`

**功能**：提供高级检索能力（语义聚类采样、多维过滤）

**核心方法**：
```python
def deep_research_retrieval(
    query: str,
    *,
    k: Optional[int] = None,
    filters: Optional[Dict[str, Any]] = None,
    query_timeout_ms: int = 2000,
) -> List[Document]:
    """
    高级检索方法：
    - 向量相似性搜索
    - 元数据多维过滤
    - 语义聚类采样（避免结果重复）
    - SQL超时保护
    """
```

**支持的过滤维度**：
```python
filters = {
    "platform": "reddit" or ["reddit", "youtube"],
    "date_from": datetime(2025, 12, 1),
    "date_to": datetime(2026, 1, 8),
    "min_score": 3.0,
}
```

---

## Dorks生成改进

### 问题分析（改进前）

原始系统生成的Dorks过度限制：
- ❌ 使用 `site:reddit.com/r/SkincareAddiction` 限定特定板块
- ❌ 多个Dorks重复指向同一源
- ❌ 总共仅6个Dorks，覆盖2-3个平台
- ❌ 最终搜索结果仅47个链接，且来源单一

**后果**：
- 信息不足，样本偏差大
- 容易陷入"社群回声室"
- 无法反映全面的市场观点

### 改进方案

#### 改进1：多源覆盖策略

引入 `_get_domain_sources()` 方法，为4个领域配置多元化源列表：

| 领域 | 社交媒体 | 电商 | 媒体/新闻 | 博客 | 论坛 |
|------|---------|------|---------|------|------|
| **B2C** | Reddit, TikTok, Instagram, YouTube | Amazon, Trustpilot, G2 | Forbes, Vogue, Business Insider | Medium, Substack | - |
| **B2B** | - | - | Bloomberg, Reuters, FT, WSJ | - | Reddit /r/business, YC |
| **Tech** | - | - | TechCrunch, ArsTechnica | Medium, Dev.to, Hashnode | GitHub, Stack Overflow |
| **Med** | - | - | - | - | PubMed, ArXiv, NIH |

#### 改进2：增加Dorks数量

- 改进前：6个
- 改进后：10个（+67%）
- 每个Dork来自不同平台

#### 改进3：多角度关键词策略

同一源的多个Dorks使用不同关键词角度：
```
site:reddit.com skincare (trends OR viral OR forecast OR controversy)
site:tiktok.com skincare marketing (2026 OR future OR trend OR consumer behavior)
site:instagram.com #skincaretrends OR #beautyviral
site:youtube.com skincare marketing (fail OR strategy OR insights)
```

### 改进效果

| 指标 | 改进前 | 改进后 | 提升 |
|------|--------|--------|------|
| Dorks数量 | 6个 | 10个 | +67% |
| 覆盖平台 | 2-3个 | 10个 | +300% |
| 搜索结果 | 47个 | 64+个 | +36% |
| 信息多样性 | 低 | 高 | ⬆️⬆️⬆️ |
| 平台集中度 | 高 | 低 | ⬇️⬇️⬇️ |

---

## 数据存储

### 数据库架构

**使用技术**：PostgreSQL + pgvector

**主要表结构**：

```sql
-- 文档表（向量存储）
CREATE TABLE documents (
    id UUID PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    title TEXT,
    content TEXT,
    platform VARCHAR(50),
    published_date TIMESTAMPTZ,
    score FLOAT,
    embedding vector(1536),  -- OpenAI embedding维度
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 创建索引
CREATE INDEX idx_platform ON documents(platform);
CREATE INDEX idx_date ON documents(published_date);
CREATE INDEX idx_score ON documents(score);
CREATE INDEX idx_embedding ON documents USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20);
```

### 索引策略

**元数据索引**：
- `platform`：支持按平台过滤
- `published_date`：支持按时间范围过滤
- `score`：支持按评分过滤

**向量索引**：
- 类型：IVFFlat（Inverted File Flat）
- Lists参数：20（平衡速度和质量）
- 距离函数：cosine（余弦相似度）
- 创建条件：数据 ≥ 100条

### 性能优化

1. **批量插入**：使用batch操作减少往返
2. **异步处理**：后台索引创建不阻塞应用
3. **连接池**：使用连接池复用数据库连接
4. **查询超时**：设置 `statement_timeout` 防止长查询

---

## 检索系统

### 检索流程

```
用户查询
    ↓
[分词/规范化]
    ↓
[向量嵌入]
    ↓
[向量相似性搜索] → 候选文档集
    ↓
[元数据过滤] → 过滤后候选
    ↓
[语义聚类采样] → 多样化采样
    ↓
[排序和去重] → 最终结果
```

### 高级特性

**1. 语义聚类采样**
- 问题：向量搜索结果可能集中在某个主题
- 解决：对结果进行聚类，从不同聚类中采样
- 优点：结果多样化，覆盖不同角度

**2. 多维度过滤**
```python
filters = {
    "platform": ["reddit", "youtube"],        # 平台过滤
    "date_from": datetime(2025, 12, 1),      # 时间范围
    "date_to": datetime(2026, 1, 8),
    "min_score": 3.0,                        # 评分阈值
}
```

**3. SQL超时保护**
```python
query_timeout_ms: int = 2000  # 查询最多等待2秒
```

---

## 生产部署

### 部署前准备

**1. 环境检查**
```bash
# Python版本
python --version  # 需要3.9+

# PostgreSQL版本
psql --version   # 需要12+

# pgvector扩展
psql -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

**2. 环境变量配置**
```bash
# 数据库连接
export PGVECTOR_URL="postgres://user:pass@host:5432/dbname"
# 或
export DATABASE_URL="postgres://user:pass@host:5432/dbname"

# LLM配置
export OPENAI_API_KEY="sk-..."

# 搜索API
export SERPER_API_KEY="..."
```

**3. 依赖安装**
```bash
pip install -r requirements.txt
```

### 初始化流程

**执行初始化脚本**：
```bash
python scripts/init_production.py
```

**脚本功能**：
1. ✅ 检查pgvector扩展
2. ✅ 创建/检查数据库表
3. ✅ 创建元数据索引
4. ⚠️ 条件性创建向量索引（数据≥100条）
5. ✅ 更新表统计信息

**初始化结果示例**：

场景1：空库
```
[INFO] ✅ pgvector 扩展已就绪
[INFO] ✅ 表已创建/已存在
[INFO] ✅ 元数据索引已创建: platform
[INFO] ✅ 元数据索引已创建: date
[INFO] ✅ 元数据索引已创建: score
[INFO] 💡 数据入库后(≥100条)，执行:
        python scripts/create_vector_index.py
```

场景2：数据充足
```
[INFO] ✅ pgvector 扩展已就绪
[INFO] ✅ 元数据索引已创建
[INFO] ✅ 向量索引已创建 (lists=20)
[INFO] ✅ 统计信息已更新
[INFO] ✅ 初始化完成，可启动应用
```

### 应用启动

```bash
# 启动API服务
python -m uvicorn main:app --host 0.0.0.0 --port 8000

# 或使用Gunicorn（生产推荐）
gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app
```

### 监控和维护

**定期检查**：
```sql
-- 检查表统计信息
SELECT schemaname, tablename, n_live_tup FROM pg_stat_user_tables;

-- 检查索引状态
SELECT indexrelname, idx_scan FROM pg_stat_user_indexes;

-- 检查向量索引性能
SELECT * FROM pg_stat_user_indexes WHERE indexrelname LIKE '%embedding%';
```

**索引维护**：
- 定期 `VACUUM` 回收空间
- 定期 `ANALYZE` 更新统计信息
- 监控索引大小和查询性能

---

## 配置参数

### EnterpriseDorkGenerator

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `model` | LLM模型 | gpt-4o |
| `temperature` | 生成温度 | 0（确定性） |

### DiscoveryService

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `max_retries` | 最大重试次数 | 3 |
| `backoff_base` | 退避基数 | 0.8 |
| `max_concurrency` | 最大并发数 | 4 |
| `min_interval` | 请求最小间隔(秒) | 0.0 |
| `timeout` | 请求超时(秒) | 15 |
| `enable_cache` | 启用缓存层 | True |
| `cache_type` | 缓存模式 (redis/file/None) | None (自动) |
| `cache_ttl` | 缓存TTL(秒) | 86400 (24h) |

### AdvancedRetriever

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `k` | 候选召回数 | 根据配置 |
| `query_timeout_ms` | 查询超时(毫秒) | 2000 |

---

## 故障处理

### 常见问题

**问题1：pgvector扩展未安装**
```
ERROR: extension "vector" does not exist
```
**解决**：
```bash
psql -d dbname -c "CREATE EXTENSION vector;"
```

**问题2：向量索引创建超时**
```
ERROR: statement_timeout exceeded
```
**解决**：
- 增加 `statement_timeout` 配置
- 分批创建索引
- 在非峰值时间执行

**问题3：搜索返回0结果**
```
原因：过滤条件过严格，或数据不足
```
**解决**：
- 检查过滤条件（platform, date, score）
- 确保数据已入库
- 尝试扩大时间范围或降低评分阈值

---

## 性能指标

### 搜索性能

| 操作 | 预期耗时 | 备注 |
|------|---------|------|
| Dorks生成（单个话题） | 2-5秒 | LLM调用 |
| 并发搜索（10个Dorks） - 冷启动 | 10-15秒 | 受API限流影响 |
| 并发搜索（10个Dorks） - 缓存命中 | <0.1秒 | 600+倍加速 |
| 向量相似性搜索 | <100ms | 基于IVFFlat索引 |
| 元数据过滤 | <50ms | 基于元数据索引 |
| 完整检索流程 | <200ms | 包括所有处理 |

### 成本指标

| 项目 | 无缓存 | 有缓存（70%命中率） | 节省 |
|------|--------|------------------|------|
| 月API调用数（假设3000次） | 3000次 | 900次 | 70% |
| 月成本 | $3.00 | $0.90 | $2.10 |
| 年成本 | $36.00 | $10.80 | $25.20 |

### 存储容量

| 指标 | 容量 | 说明 |
|------|------|------|
| 最大文档数 | 百万级+ | PostgreSQL容量 |
| 向量维度 | 1536 | OpenAI embedding |
| 索引大小 | ~1.5GB/100万条 | 向量索引 |

---

## 扩展方向

### 短期（1-3个月）
- [x] **成本控制与缓存机制**（已完成）
  - Redis/文件双模式缓存
  - 24小时TTL策略
  - 成本监控和统计
- [ ] 支持更多平台源配置
- [ ] 自动化关键词优化反馈
- [ ] 搜索结果质量评分系统

### 中期（3-6个月）
- [ ] 动态源权重调整
- [ ] 多语言支持
- [ ] 高级聚类和主题提取

### 长期（6-12个月）
- [ ] 实时数据流处理
- [ ] 知识图谱构建
- [ ] 跨域知识融合

---

**最后更新**：2026年1月8日
**版本**：1.0
**维护者**：Research Team
