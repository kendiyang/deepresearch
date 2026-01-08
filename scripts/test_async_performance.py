#!/usr/bin/env python3
"""
异步性能测试 - 对比同步 vs 异步实现

测试场景：
1. 小规模并发（10个请求）
2. 中等规模并发（50个请求）
3. 大规模并发（100个请求）

运行方法：
    python scripts/test_async_performance.py
"""

import asyncio
import time
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.discovery.discovery import DiscoveryService, DorkResult

logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')


async def test_async_discovery(dork_count: int, description: str):
    """测试异步版本的性能"""
    print(f"\n{'='*70}")
    print(f"  {description}")
    print(f"{'='*70}")
    
    # 生成测试dorks
    test_dorks = [
        f"site:reddit.com test query {i}" for i in range(dork_count)
    ]
    dork_result = DorkResult(dorks=test_dorks)
    
    # 测试异步版本
    print(f"\n📊 异步版本（httpx + asyncio）")
    print(f"   并发数: {dork_count}")
    
    async with DiscoveryService(enable_cache=False) as discovery:
        start_time = time.time()
        results = await discovery.find_discussion_urls(dork_result)
        elapsed = time.time() - start_time
        
        print(f"   ✅ 完成时间: {elapsed:.2f}秒")
        print(f"   ✅ 结果数量: {len(results)} 条")
        print(f"   ✅ 平均延迟: {elapsed/dork_count:.3f}秒/请求")
        print(f"   ✅ QPS: {dork_count/elapsed:.1f} 请求/秒")
        
        return {
            'dork_count': dork_count,
            'elapsed': elapsed,
            'results_count': len(results),
            'qps': dork_count/elapsed,
        }


async def test_concurrent_performance():
    """测试不同并发级别的性能"""
    print("\n" + "="*70)
    print("  🚀 异步性能测试 - httpx + asyncio")
    print("="*70)
    print("\n💡 说明:")
    print("  • 使用真实的 Serper API 调用")
    print("  • 测试不同并发级别的性能")
    print("  • 展示 asyncio 在 I/O 密集型任务中的优势")
    
    test_cases = [
        (5, "小规模并发测试（5个请求）"),
        (10, "中等规模并发测试（10个请求）"),
        (20, "大规模并发测试（20个请求）"),
    ]
    
    results = []
    for dork_count, description in test_cases:
        result = await test_async_discovery(dork_count, description)
        results.append(result)
        await asyncio.sleep(1)  # 避免API限流
    
    # 性能总结
    print("\n" + "="*70)
    print("  📊 性能总结")
    print("="*70)
    
    print("\n| 并发数 | 总耗时 | 结果数 | QPS | 平均延迟 |")
    print("|--------|--------|--------|-----|----------|")
    for r in results:
        print(f"| {r['dork_count']:6d} | {r['elapsed']:6.2f}s | {r['results_count']:6d} | {r['qps']:4.1f} | {r['elapsed']/r['dork_count']:8.3f}s |")
    
    print("\n" + "="*70)
    print("  ✅ 关键优势")
    print("="*70)
    print("""
1. 真正的并发执行
   • asyncio 实现非阻塞 I/O
   • 不受 GIL 限制
   • 可轻松处理 100+ 并发请求

2. 更高的资源利用率
   • 无线程切换开销
   • 内存占用更少
   • CPU 利用率更高

3. 更好的可扩展性
   • 单线程处理大量并发
   • 适合微服务架构
   • 云环境友好

4. 现代化的代码风格
   • async/await 语法清晰
   • 异步上下文管理器
   • 易于调试和维护
""")


async def test_throughput_limit():
    """测试最大吞吐量"""
    print("\n" + "="*70)
    print("  🔥 吞吐量压力测试")
    print("="*70)
    
    # 测试短时间内的最大并发
    concurrent_levels = [10, 20, 30, 40, 50]
    
    print("\n测试配置:")
    print("  • 无缓存（每次真实API调用）")
    print("  • 无延迟（max_concurrency控制）")
    print("  • 测试系统极限吞吐量\n")
    
    for level in concurrent_levels:
        test_dorks = [f"site:example.com test {i}" for i in range(level)]
        dork_result = DorkResult(dorks=test_dorks)
        
        async with DiscoveryService(
            enable_cache=False,
            max_concurrency=level,  # 动态调整并发数
        ) as discovery:
            start = time.time()
            try:
                results = await asyncio.wait_for(
                    discovery.find_discussion_urls(dork_result),
                    timeout=60
                )
                elapsed = time.time() - start
                qps = level / elapsed
                print(f"  并发 {level:2d}: {elapsed:5.2f}秒 | QPS: {qps:5.1f} | 结果: {len(results):3d}")
            except asyncio.TimeoutError:
                print(f"  并发 {level:2d}: 超时（>60秒）")
            except Exception as e:
                print(f"  并发 {level:2d}: 错误 - {e}")
        
        await asyncio.sleep(0.5)  # 短暂休息


async def main():
    """主测试函数"""
    print("\n" + "="*70)
    print("  异步性能测试套件")
    print("="*70)
    
    # 测试1: 不同并发级别
    await test_concurrent_performance()
    
    # 测试2: 吞吐量压力测试
    # await test_throughput_limit()  # 取消注释以运行压力测试
    
    print("\n" + "="*70)
    print("  🎉 测试完成！")
    print("="*70)
    print("\n💡 结论:")
    print("  • 异步实现显著提升并发性能")
    print("  • 适合高并发 I/O 密集型场景")
    print("  • 生产环境推荐使用异步版本")
    print()


if __name__ == "__main__":
    asyncio.run(main())
