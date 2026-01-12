#!/usr/bin/env python3
"""
缓存功能完整测试脚本（异步版本）

测试场景：
1. 缓存层基础功能（读写、过期、统计）
2. DiscoveryService集成测试（API调用 vs 缓存命中）
3. 成本对比分析
4. 并发场景下的缓存性能

运行方法：
    python scripts/test_cache_integration.py
"""

import os
import sys
import time
import logging
import shutil
import asyncio
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.discovery.discovery import DiscoveryService, DorkResult
from services.search.cache import SearchCache, CacheMetrics

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def test_cache_basics():
    """测试 1: 缓存层基础功能"""
    print("\n" + "="*70)
    print("测试 1: 缓存层基础功能")
    print("="*70)
    
    cache = SearchCache(cache_type='file', cache_dir='./cache/test_basic')
    
    # 测试写入
    test_dork = 'site:reddit.com skincare trends'
    test_data = {
        'organic': [
            {'link': 'https://example.com/1', 'title': 'Test 1'},
            {'link': 'https://example.com/2', 'title': 'Test 2'},
        ]
    }
    
    assert cache.set(test_dork, test_data), "❌ 缓存写入失败"
    print("✓ 缓存写入成功")
    
    # 测试读取
    result = cache.get(test_dork)
    assert result is not None, "❌ 缓存读取失败"
    assert len(result['organic']) == 2, "❌ 缓存内容不完整"
    print("✓ 缓存读取成功")
    
    # 测试缓存未命中
    miss_result = cache.get('site:example.com nonexistent')
    assert miss_result is None, "❌ 应该返回 None"
    print("✓ 缓存未命中处理正确")
    
    # 测试统计
    stats = cache.stats()
    assert stats['cache_type'] == 'file', "❌ 缓存类型错误"
    assert stats['total_files'] >= 1, "❌ 缓存文件数量错误"
    print(f"✓ 缓存统计: {stats['total_files']} 个文件, {stats['total_size_mb']} MB")
    
    print("\n✅ 测试 1 通过: 缓存层基础功能正常")
    return True


def test_discovery_integration():
    """测试 2: DiscoveryService 集成（异步）"""
    print("\n" + "="*70)
    print("测试 2: DiscoveryService 集成测试（异步）")
    print("="*70)
    
    async def run_test():
        # 清空缓存
        cache_dir = './cache/serper'
        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir)
        print(f"✓ 清空缓存目录: {cache_dir}")
        
        # 准备固定的 dorks
        test_dorks = [
            "site:reddit.com skincare trends 2026",
            "site:youtube.com beauty marketing viral",
            "site:forbes.com cosmetics industry",
        ]
        dork_result = DorkResult(dorks=test_dorks)
        
        # 第一次运行（API调用）
        print("\n--- 第一次运行（冷启动，调用API）---")
        async with DiscoveryService(enable_cache=True, cache_type='file') as discovery1:
            start_time = time.time()
            results1 = await discovery1.find_discussion_urls(dork_result)
            elapsed1 = time.time() - start_time
            
            stats1 = discovery1.metrics.get_stats()
            print(f"  结果数量: {len(results1)} 条")
            print(f"  API调用: {stats1['api_calls']} 次")
            print(f"  缓存命中: {stats1['cache_hits']} 次")
            print(f"  耗时: {elapsed1:.2f}秒")
            print(f"  成本: ${stats1['estimated_cost_usd']}")
            
            # 验证第一次运行
            assert stats1['api_calls'] == len(test_dorks), "❌ API调用次数不符"
            assert stats1['cache_hits'] == 0, "❌ 首次运行不应有缓存命中"
        
        # 第二次运行（缓存命中）
        print("\n--- 第二次运行（缓存命中）---")
        async with DiscoveryService(enable_cache=True, cache_type='file') as discovery2:
            start_time = time.time()
            results2 = await discovery2.find_discussion_urls(dork_result)
            elapsed2 = time.time() - start_time
            
            stats2 = discovery2.metrics.get_stats()
            print(f"  结果数量: {len(results2)} 条")
            print(f"  API调用: {stats2['api_calls']} 次")
            print(f"  缓存命中: {stats2['cache_hits']} 次")
            print(f"  命中率: {stats2['hit_rate_percent']}%")
            print(f"  耗时: {elapsed2:.2f}秒")
            print(f"  节省成本: ${stats2['cost_saved_usd']} 💰")
            
            # 验证第二次运行
            assert stats2['api_calls'] == 0, "❌ 不应有API调用"
            assert stats2['cache_hits'] == len(test_dorks), "❌ 缓存命中次数不符"
            assert stats2['hit_rate_percent'] == 100.0, "❌ 命中率应为100%"
            assert len(results1) == len(results2), "❌ 两次结果数量应相同"
            
            # 性能对比
            speedup = elapsed1 / elapsed2 if elapsed2 > 0 else float('inf')
            print(f"\n📊 性能对比:")
            print(f"  加速比: {speedup:.1f}x")
            print(f"  时间节省: {(elapsed1 - elapsed2):.2f}秒")
    
    # 运行异步测试
    asyncio.run(run_test())
    
    print("\n✅ 测试 2 通过: DiscoveryService 集成正常，缓存命中率100%")
    return True


def test_cost_analysis():
    """测试 3: 成本分析"""
    print("\n" + "="*70)
    print("测试 3: 成本对比分析")
    print("="*70)
    
    # 模拟一个月的使用场景
    daily_topics = 10  # 每天10个topic
    dorks_per_topic = 10  # 每个topic生成10个dorks
    days_per_month = 30
    cache_hit_rate = 0.7  # 假设70%缓存命中率（重复topic）
    
    total_requests = daily_topics * dorks_per_topic * days_per_month
    api_cost_per_1k = 1.0  # Serper: $1/1000次
    
    # 无缓存成本
    no_cache_cost = total_requests * (api_cost_per_1k / 1000)
    
    # 有缓存成本
    api_calls = total_requests * (1 - cache_hit_rate)
    with_cache_cost = api_calls * (api_cost_per_1k / 1000)
    
    # 节省
    saved_cost = no_cache_cost - with_cache_cost
    saved_percent = (saved_cost / no_cache_cost * 100)
    
    print(f"\n📊 月度成本分析（假设场景）:")
    print(f"  每日Topic数: {daily_topics}")
    print(f"  每Topic的Dorks数: {dorks_per_topic}")
    print(f"  月总请求数: {total_requests:,}")
    print(f"  假设缓存命中率: {cache_hit_rate*100:.0f}%")
    print(f"\n  无缓存成本: ${no_cache_cost:.2f}/月")
    print(f"  有缓存成本: ${with_cache_cost:.2f}/月")
    print(f"  节省金额: ${saved_cost:.2f}/月 ({saved_percent:.0f}%)")
    print(f"  年节省: ${saved_cost * 12:.2f}")
    
    print("\n💡 成本优化建议:")
    print("  1. 生产环境建议使用 Redis（分布式共享缓存）")
    print("  2. 设置合理的 TTL（24小时）平衡时效性与成本")
    print("  3. 监控缓存命中率，优化搜索策略")
    print("  4. 对热门topic可延长缓存时间")
    
    print("\n✅ 测试 3 完成: 成本分析显示缓存收益显著")
    return True


def test_cache_expiration():
    """测试 4: 缓存过期机制"""
    print("\n" + "="*70)
    print("测试 4: 缓存过期机制")
    print("="*70)
    
    # 创建短TTL的缓存（2秒）
    cache = SearchCache(
        cache_type='file',
        cache_dir='./cache/test_ttl',
        ttl_seconds=2
    )
    
    test_dork = 'site:example.com test expiration'
    test_data = {'organic': [{'link': 'https://example.com', 'title': 'Test'}]}
    
    # 写入缓存
    cache.set(test_dork, test_data)
    print("✓ 写入缓存，TTL=2秒")
    
    # 立即读取（应该命中）
    result = cache.get(test_dork)
    assert result is not None, "❌ 立即读取应该命中"
    print("✓ 立即读取: 缓存命中")
    
    # 等待3秒后读取（应该过期）
    print("  等待3秒...")
    time.sleep(3)
    result = cache.get(test_dork)
    assert result is None, "❌ 过期后应该返回 None"
    print("✓ 3秒后读取: 缓存已过期")
    
    print("\n✅ 测试 4 通过: 缓存过期机制正常")
    return True


def main():
    """运行所有测试"""
    print("\n" + "="*70)
    print("  🚀 缓存功能完整测试套件")
    print("="*70)
    
    tests = [
        ("缓存层基础功能", test_cache_basics),
        ("DiscoveryService集成", test_discovery_integration),
        ("成本对比分析", test_cost_analysis),
        ("缓存过期机制", test_cache_expiration),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            if test_func():
                passed += 1
        except Exception as e:
            failed += 1
            logger.error(f"❌ 测试失败: {name}")
            logger.exception(e)
    
    # 最终总结
    print("\n" + "="*70)
    print("  📊 测试总结")
    print("="*70)
    print(f"  总测试数: {len(tests)}")
    print(f"  通过: {passed} ✅")
    print(f"  失败: {failed} ❌")
    
    if failed == 0:
        print("\n🎉 所有测试通过！缓存层已就绪，可用于生产环境。")
        return 0
    else:
        print(f"\n⚠️  有 {failed} 个测试失败，请检查日志。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
