import asyncio
import logging
from services.search.dorks import EnterpriseDorkGenerator
from services.search.search import SearchService
from services.search.translate import IntentQuery 

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    async def main():
        generator = EnterpriseDorkGenerator()
        
        # 使用异步上下文管理器
        async with SearchService() as discovery:
            #test_topic = "北美市场护肤品营销趋势分析"
            test_topic = "阿曼绿乳香精油 —— 黄金级淡纹紧致修复力、抗皱力。比普通的眼部精华油增加了皇家顶级的珍稀抗皱精油配方"
            result = await IntentQuery().translate(test_topic)
            queries = result.queries
            
            dork_result = []
            
            for q in queries:
                logging.info(f"Generated Query: {q}")
                dork_result.extend((await generator.run(q)).dorks)
            print(f"\n生成的 Dorks:")
            for i, dork in enumerate(dork_result, 1):
                print(f"  {i}. {dork}")
            
            results = await discovery.find_discussion_urls(dork_result)
            print(f"\n示例: {test_topic}")
            print(f"返回 {len(results)} 条结果（去重后）")
            for item in results:
                print(" -", item.url)
    
    # 运行异步主函数
    asyncio.run(main())