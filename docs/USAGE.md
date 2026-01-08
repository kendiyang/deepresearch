# Deep Research 系统 - 使用指南

## 目录
1. [快速开始](#快速开始)
2. [搜索策略生成](#搜索策略生成)
3. [多源数据采集](#多源数据采集)
4. [向量存储和检索](#向量存储和检索)
5. [生产部署](#生产部署)
6. [常见用例](#常见用例)
7. [故障排除](#故障排除)

---

## 快速开始

### 安装和配置

**1. 环境准备**
```bash
# Python 3.9+
python --version

# PostgreSQL 12+
psql --version

# 克隆项目
git clone <repo>
cd deepresearch

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# 或
.\.venv\Scripts\activate   # Windows

# 安装依赖
pip install -r requirements.txt
```

**2. 环境变量配置**
```bash
# 创建 .env 文件
export PGVECTOR_URL="postgres://user:password@localhost:5432/deepresearch"
export OPENAI_API_KEY="sk-your-key"
export SERPER_API_KEY="your-serper-key"
```

**3. 数据库初始化**
```bash
# 一键初始化
python scripts/init_production.py

# 预期输出：
# ✅ pgvector 扩展已就绪
# ✅ 元数据索引已创建
# ✅ 初始化完成
```

### 第一个搜索任务

```python
from services.discovery.discovery import EnterpriseDorkGenerator, DiscoveryService

# 1️⃣ 生成搜索策略（Dorks）
generator = EnterpriseDorkGenerator()
topic = "北美市场2026年护肤品营销趋势分析"
dork_result = generator.run(topic)

# 2️⃣ 执行多源搜索
discovery = DiscoveryService()
results = discovery.find_discussion_urls(dork_result, per_dork=20)

# 3️⃣ 处理结果
print(f"找到 {len(results)} 个链接：")
for item in results[:5]:
    print(f"  - {item.url}")
    print(f"    来源: {item.domain}")
    print(f"    标题: {item.title}")
```

**预期输出**：
```
📊 策略识别: [B2C_CONSUMER] 关键词: ['skincare marketing trends 2026', ...]
🎯 锁定平台: ['site:reddit.com', 'site:tiktok.com', ...]

生成的 Dorks:
  1. site:reddit.com skincare (trends OR viral OR forecast)
  2. site:tiktok.com skincare marketing (2026 OR future)
  ...

找到 64 个链接：
  - https://reddit.com/r/skincare/comments/...
    来源: reddit.com
    标题: Best skincare trends in 2026
  ...
```

---

## 搜索策略生成

### 搜索流程详解

#### Phase 1：战略分析

系统会自动识别研究话题属于哪个领域：

**B2C消费品** - 适用于：
- 产品营销趋势
- 消费者行为分析
- 品牌评价反馈
- 病毒营销现象

```python
topic = "2026年护肤品营销趋势"
# → 自动识别为 B2C_CONSUMER 领域
# → 生成关键词: skincare marketing trends 2026
# → 选择平台: Reddit, TikTok, Instagram, YouTube, Forbes, Medium
```

**B2B企业** - 适用于：
- SaaS采购决策
- 企业工具评估
- ROI分析
- 供应链管理

```python
topic = "企业SaaS采购决策因素"
# → 自动识别为 B2B_ENTERPRISE 领域
# → 生成关键词: enterprise SaaS procurement
# → 选择平台: LinkedIn, G2, Reuters, Bloomberg
```

**技术开发** - 适用于：
- 编程问题解决
- 技术性能优化
- 框架库评估
- 最佳实践

```python
topic = "Python内存管理优化"
# → 自动识别为 TECHNICAL_DEV 领域
# → 生成关键词: python memory optimization
# → 选择平台: GitHub, Stack Overflow, Dev.to
```

**学术医学** - 适用于：
- 临床研究
- 药物安全性
- 医学突破
- 疾病机制

```python
topic = "GLP-1长期副作用机制研究"
# → 自动识别为 ACADEMIC_MEDICAL 领域
# → 生成关键词: GLP-1 long term side effects
# → 选择平台: PubMed, NIH, ArXiv
```

#### Phase 2：Dorks生成

系统生成10个多样化的Google Dorks，每个都来自不同的平台和角度：

```python
# 自动生成的10个Dorks示例：
[
  "site:reddit.com skincare (trends OR viral OR forecast)",
  "site:tiktok.com skincare marketing (2026 OR future)",
  "site:instagram.com #skincaretrends2026",
  "site:youtube.com skincare marketing fail",
  "site:amazon.com/reviews skincare regret problem",
  "site:trustpilot.com skincare review",
  "site:voguebusiness.com skincare marketing",
  "site:forbes.com skincare trends 2026",
  "site:businessinsider.com beauty industry",
  "site:medium.com skincare industry insights",
]
```

### 自定义搜索

**调整话题表述**：
```python
# ❌ 太泛
topic = "护肤品"

# ✅ 清晰具体
topic = "北美市场2026年护肤品营销趋势"
```

**控制搜索深度**：
```python
# 修改生成的Dorks数量
# 编辑 services/discovery/discovery.py 中的提示词：
# "Output exactly 15 dorks..."  # 改为15个
```

**添加自定义源**：
```python
# 在 _get_domain_sources() 中添加新源
generator._get_domain_sources("B2C_CONSUMER")
# 返回源配置字典，可以修改或扩展
```

---

## 多源数据采集

### 搜索执行

```python
from services.discovery.discovery import DiscoveryService, DorkResult

# 初始化搜索服务
discovery = DiscoveryService(
    max_concurrency=4,      # 最多并发4个搜索
    timeout=15,             # 单个请求15秒超时
    max_retries=3,          # 失败重试3次
)

# 执行搜索（10个Dorks并发执行）
results = discovery.find_discussion_urls(
    dork_result,
    per_dork=20             # 每个Dork获取20条结果
)

# 结果数据结构
for item in results:
    print(f"""
    URL: {item.url}
    Domain: {item.domain}      # reddit.com / tiktok.com / ...
    Title: {item.title}
    Rank: {item.rank}          # 在搜索结果中的排名
    Dork: {item.dork}          # 来自哪个Dork
    """)
```

### 结果分析

**按平台统计**：
```python
from collections import Counter

domains = Counter(item.domain for item in results)
print("各平台结果分布：")
for domain, count in domains.most_common(10):
    print(f"  {domain}: {count}条")

# 输出示例：
#   reddit.com: 12条
#   tiktok.com: 11条
#   instagram.com: 10条
#   youtube.com: 9条
#   amazon.com: 8条
#   ...
```

**按Dork统计**：
```python
dork_hits = Counter(item.dork for item in results)
print("各Dork的效果：")
for dork, count in dork_hits.most_common(5):
    dork_display = dork[:60] + "..." if len(dork) > 60 else dork
    print(f"  {count:2d}条 - {dork_display}")
```

**按内容质量筛选**：
```python
# 只保留标题信息完整的结果
quality_results = [item for item in results if item.title and len(item.title) > 10]

# 按平台筛选
reddit_results = [item for item in results if "reddit.com" in item.domain]
tiktok_results = [item for item in results if "tiktok.com" in item.domain]
```

---

## 向量存储和检索

### 存储文档

```python
from services.knowledge.storageAndRetrieval import (
    storageAndRetrieval,
    Document
)

# 初始化存储服务
storage = storageAndRetrieval()

# 准备文档列表
documents = [
    Document(
        url="https://reddit.com/r/...",
        title="Skincare Trends 2026",
        content="The upcoming trends in skincare...",
        platform="reddit",
        published_date="2026-01-08",
        score=4.5,
        metadata={"subreddit": "SkincareAddiction"}
    ),
    # ... 更多文档
]

# 批量插入
storage.insert_documents(documents)

# ✅ 文档已存储并向量化
# ✅ 元数据索引已建立
```

### 向量检索

```python
# 基础向量相似性搜索
results = storage.retrieve_similar(
    query="skincare trends 2026",
    k=10  # 返回前10最相似的文档
)

for doc in results:
    print(f"相似度分数: {doc.similarity_score:.3f}")
    print(f"标题: {doc.title}")
    print(f"URL: {doc.url}\n")
```

### 高级检索：多维度过滤

```python
from services.knowledge import AdvancedRetriever
from datetime import datetime, timezone

retriever = AdvancedRetriever()

# 高级检索：查询 + 过滤 + 采样
results = retriever.deep_research_retrieval(
    query="skincare trends and marketing strategies",
    
    # 可选：限制候选召回数
    k=200,
    
    # 可选：多维度过滤
    filters={
        # 平台过滤（单个或列表）
        "platform": ["reddit", "youtube", "tiktok"],
        
        # 时间范围过滤
        "date_from": datetime(2024, 12, 1, tzinfo=timezone.utc),
        "date_to": datetime(2026, 1, 8, tzinfo=timezone.utc),
        
        # 评分阈值
        "min_score": 3.0,
    },
    
    # 可选：查询超时
    query_timeout_ms=2000,
)

print(f"检索到 {len(results)} 条结果")
for doc in results:
    print(f"  - {doc.title} ({doc.platform})")
```

**过滤选项详解**：

| 过滤字段 | 类型 | 说明 | 示例 |
|---------|------|------|------|
| `platform` | str 或 List[str] | 单个或多个平台 | "reddit" 或 ["reddit", "youtube"] |
| `date_from` | datetime | 开始时间 | datetime(2024, 12, 1) |
| `date_to` | datetime | 结束时间 | datetime(2026, 1, 8) |
| `min_score` | float | 最低评分 | 3.0 |

### 检索工作流完整示例

```python
from services.discovery.discovery import EnterpriseDorkGenerator, DiscoveryService
from services.knowledge.storageAndRetrieval import storageAndRetrieval, Document
from services.knowledge import AdvancedRetriever
from datetime import datetime, timezone

# ========== Step 1: 生成搜索策略 ==========
generator = EnterpriseDorkGenerator()
dork_result = generator.run("北美护肤品营销趋势2026")
print(f"✅ 生成了 {len(dork_result.dorks)} 个Dorks")

# ========== Step 2: 执行多源搜索 ==========
discovery = DiscoveryService()
discovery_results = discovery.find_discussion_urls(dork_result)
print(f"✅ 找到 {len(discovery_results)} 个链接")

# ========== Step 3: 存储搜索结果 ==========
storage = storageAndRetrieval()
documents = [
    Document(
        url=item.url,
        title=item.title or "No title",
        content="",  # 实际应用中应该爬取内容
        platform=item.domain.split('.')[0],  # 提取主域名
        published_date=datetime.now(timezone.utc).isoformat(),
        score=float(item.rank),
        metadata={"source_dork": item.dork}
    )
    for item in discovery_results
]
storage.insert_documents(documents)
print(f"✅ 存储了 {len(documents)} 个文档")

# ========== Step 4: 执行高级检索 ==========
retriever = AdvancedRetriever()
final_results = retriever.deep_research_retrieval(
    query="skincare marketing trends forecast 2026",
    k=100,
    filters={
        "platform": ["reddit", "tiktok", "youtube"],
        "min_score": 2.0,
    }
)
print(f"✅ 检索到 {len(final_results)} 条最相关结果")

# ========== Step 5: 分析结果 ==========
for i, doc in enumerate(final_results[:5], 1):
    print(f"\n{i}. {doc.title}")
    print(f"   平台: {doc.platform}")
    print(f"   相关度: {doc.similarity_score:.3f}")
    print(f"   URL: {doc.url}")
```

---

## 生产部署

### 一键部署

**Linux/macOS**：
```bash
# 1. 环境变量
export PGVECTOR_URL="postgres://user:pass@host:5432/db"
export OPENAI_API_KEY="sk-..."

# 2. 初始化
python scripts/init_production.py

# 3. 启动应用
python -m uvicorn main:app --host 0.0.0.0 --port 8000

# 4. 验证
curl http://localhost:8000/health
```

### Docker部署

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

ENV PGVECTOR_URL=postgres://user:pass@db:5432/deepresearch
ENV OPENAI_API_KEY=sk-your-key

# 初始化数据库
RUN python scripts/init_production.py

# 启动应用
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0"]
```

**构建和运行**：
```bash
docker build -t deepresearch .
docker run -p 8000:8000 \
  -e PGVECTOR_URL="postgres://..." \
  -e OPENAI_API_KEY="sk-..." \
  deepresearch
```

### 性能监控

**监控数据库健康**：
```sql
-- 检查表大小
SELECT 
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
FROM pg_tables
WHERE schemaname = 'public';

-- 检查索引效率
SELECT 
    indexrelname,
    idx_scan as scans,
    idx_tup_read as reads,
    idx_tup_fetch as fetches
FROM pg_stat_user_indexes
ORDER BY idx_scan DESC;
```

**应用日志**：
```bash
# 查看最近的日志
tail -f /var/log/deepresearch/app.log

# 搜索错误
grep ERROR /var/log/deepresearch/app.log
```

---

## 常见用例

### 用例1：市场趋势研究

**目标**：了解2026年护肤品市场的营销趋势

```python
# 运行搜索和分析
topic = "2026年北美护肤品营销趋势和消费者偏好"
generator = EnterpriseDorkGenerator()
dork_result = generator.run(topic)

discovery = DiscoveryService()
results = discovery.find_discussion_urls(dork_result)

# 分析结果
from collections import Counter

# 1. 按平台分析
platforms = Counter(item.domain for item in results)
print("消息来源多样性：")
for platform, count in platforms.most_common():
    print(f"  {platform}: {count}条")

# 2. 存储到向量数据库
storage = storageAndRetrieval()
# ... 准备documents列表 ...
storage.insert_documents(documents)

# 3. 执行语义检索
retriever = AdvancedRetriever()
market_trends = retriever.deep_research_retrieval(
    query="skincare trends consumer preferences 2026",
    filters={"platform": ["reddit", "tiktok"]}
)

# 4. 提取关键发现
print("\n主要趋势发现：")
for doc in market_trends[:5]:
    print(f"  - {doc.title}")
```

### 用例2：竞品分析

**目标**：分析竞品的市场评价和用户反馈

```python
topic = "品牌X护肤产品用户评价和竞争优势"
generator = EnterpriseDorkGenerator()
dork_result = generator.run(topic)

discovery = DiscoveryService()
results = discovery.find_discussion_urls(dork_result)

# 按平台分类处理
for item in results:
    if "amazon" in item.domain or "trustpilot" in item.domain:
        print(f"评价平台: {item.url}")
    elif "reddit" in item.domain:
        print(f"用户讨论: {item.url}")
    elif "youtube" in item.domain:
        print(f"视频评测: {item.url}")
```

### 用例3：技术问题排查

**目标**：找出某个技术问题的解决方案

```python
topic = "Python asyncio性能优化最佳实践"
generator = EnterpriseDorkGenerator()
dork_result = generator.run(topic)

discovery = DiscoveryService()
results = discovery.find_discussion_urls(dork_result)

# 按来源类型优先级排列
priorities = {
    "stackoverflow.com": 1,
    "github.com": 2,
    "dev.to": 3,
    "medium.com": 4,
    "reddit.com": 5,
}

sorted_results = sorted(
    results,
    key=lambda x: priorities.get(x.domain, 999)
)

# 优先查看高质量来源
for item in sorted_results[:10]:
    print(f"{item.domain}: {item.title}")
```

---

## 故障排除

### 问题1：Dorks生成失败

**症状**：
```
ERROR: Failed to parse LLM response
```

**原因**：
- LLM API未响应
- 网络连接问题
- API密钥无效

**解决**：
```bash
# 检查API密钥
echo $OPENAI_API_KEY

# 检查网络连接
curl https://api.openai.com/v1/models

# 查看完整错误日志
python -c "import logging; logging.basicConfig(level=logging.DEBUG)"
```

### 问题2：搜索返回结果为空

**症状**：
```
INFO: 汇总后有效链接: 0
```

**原因**：
- Dorks语法不正确
- 搜索词在该平台不存在
- Serper API限流或故障

**解决**：
```python
# 手动检查生成的Dorks
for i, dork in enumerate(dork_result.dorks, 1):
    print(f"{i}. {dork}")

# 尝试简化搜索词
topic = "护肤品"  # 而不是复杂的长句子

# 增加重试次数
discovery = DiscoveryService(max_retries=5)
```

### 问题3：数据库连接失败

**症状**：
```
ERROR: connection refused
```

**原因**：
- PostgreSQL未启动
- 连接字符串错误
- 防火墙阻止

**解决**：
```bash
# 检查PostgreSQL状态
pg_isready -h localhost -p 5432

# 测试连接字符串
psql $PGVECTOR_URL -c "SELECT 1;"

# 查看完整的连接信息
echo $PGVECTOR_URL
```

### 问题4：向量索引创建超时

**症状**：
```
ERROR: statement_timeout exceeded
```

**原因**：
- 数据量太大
- 默认超时时间太短

**解决**：
```bash
# 手动创建索引，设置更长的超时
psql $PGVECTOR_URL -c "SET statement_timeout = '600s';" \
  -c "CREATE INDEX idx_embedding ON documents USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20);"

# 或分批创建
python scripts/create_vector_index.py --batch-size 10000
```

### 问题5：检索结果不相关

**症状**：
```
检索结果与查询不匹配
```

**原因**：
- 向量模型与输入文本不匹配
- 嵌入质量不足
- 过滤条件太宽松

**解决**：
```python
# 1. 尝试更详细的查询
query = "skincare marketing trends 2026"  # 更具体

# 2. 加强过滤条件
filters = {
    "platform": ["reddit", "tiktok"],
    "date_from": datetime(2025, 12, 1),
    "min_score": 4.0,
}

# 3. 减少候选召回数
results = retriever.deep_research_retrieval(query, k=50)

# 4. 检查嵌入质量
print(f"前几个相似度分数:")
for doc in results[:3]:
    print(f"  {doc.similarity_score:.3f} - {doc.title}")
```

### 获取帮助

**查看日志**：
```bash
# 应用日志
grep "ERROR\|WARNING" deepresearch.log

# 数据库日志
tail -f /var/log/postgresql/postgresql.log
```

**测试脚本**：
```bash
# 测试Dorks生成
python -c "
from services.discovery.discovery import EnterpriseDorkGenerator
gen = EnterpriseDorkGenerator()
result = gen.run('test topic')
print(result.dorks)
"

# 测试数据库连接
python -c "
import psycopg2
conn = psycopg2.connect(os.getenv('PGVECTOR_URL'))
print('✅ Database connected')
"
```

---

## 最佳实践

### 1. 研究话题优化
- ✅ 清晰具体：不要只说"护肤品"
- ✅ 包含关键信息：时间、地域、维度
- ✅ 避免歧义：避免可能被误解的词汇

```python
# ❌ 不好
topic = "护肤品"

# ✅ 好
topic = "2026年北美纯净美妆营销趋势和消费者行为变化"
```

### 2. 结果质量保证
- ✅ 检查来源多样性
- ✅ 验证信息准确性（跨多个源）
- ✅ 记录数据收集时间

```python
# 分析结果质量
from collections import Counter
platforms = Counter(item.domain for item in results)
diversity_score = len(platforms) / len(results)
print(f"多样性分数: {diversity_score:.2%}")
```

### 3. 成本优化
- 🔄 重用已生成的Dorks
- 🎯 使用过滤条件减少后续处理
- 📊 批量处理多个话题

```python
# 一次生成多个话题的Dorks
topics = [
    "护肤品趋势2026",
    "彩妆营销策略",
    "个护消费行为",
]

all_dorks = []
for topic in topics:
    dork_result = generator.run(topic)
    all_dorks.extend(dork_result.dorks)

# 合并执行
results = discovery.find_discussion_urls_batch(all_dorks)
```

### 4. 定期维护
- 📅 定期更新数据库统计信息
- 🧹 清理过期数据
- 📊 监控索引性能

```bash
# 每周运行
python scripts/maintenance.py  # 虚拟脚本

# 手动运行维护
psql $PGVECTOR_URL -c "VACUUM ANALYZE documents;"
```

---

**最后更新**：2026年1月8日
**版本**：1.0
**维护者**：Research Team
