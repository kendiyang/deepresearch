#!/usr/bin/env python3
"""
测试改进后的Dorks生成策略在不同研究领域的表现
"""
import sys
sys.path.insert(0, '/Users/mg/Workspace/deepresearch')

from services.discovery.discovery import EnterpriseDorkGenerator
import json

def test_dorks_generation():
    """测试多个不同领域的话题"""
    generator = EnterpriseDorkGenerator()
    
    # 测试用例覆盖四个不同的领域
    test_topics = [
        {
            "topic": "北美市场2026年护肤品营销趋势分析",
            "expected_domain": "B2C_CONSUMER",
            "description": "B2C消费品趋势分析"
        },
        {
            "topic": "企业SaaS采购决策因素和ROI评估",
            "expected_domain": "B2B_ENTERPRISE",
            "description": "B2B企业软件采购"
        },
        {
            "topic": "Python内存管理优化和性能调试",
            "expected_domain": "TECHNICAL_DEV",
            "description": "技术开发问题"
        },
        {
            "topic": "GLP-1长期副作用机制研究",
            "expected_domain": "ACADEMIC_MEDICAL",
            "description": "学术医学研究"
        }
    ]
    
    print("=" * 80)
    print("Dorks生成策略测试报告")
    print("=" * 80)
    
    for test_case in test_topics:
        print(f"\n📋 测试用例: {test_case['description']}")
        print(f"   话题: {test_case['topic']}")
        print("-" * 80)
        
        try:
            result = generator.run(test_case['topic'])
            
            print(f"✅ 生成成功")
            print(f"   总Dorks数: {len(result.dorks)}")
            
            # 分析Dorks的多样性
            sites = set()
            keywords_patterns = {}
            
            for i, dork in enumerate(result.dorks, 1):
                # 提取site:
                if "site:" in dork:
                    site_part = dork.split("site:")[1].split()[0]
                    sites.add(site_part)
                
                # 提取关键词特征
                if "OR" in dork:
                    keywords_patterns["OR_operators"] = keywords_patterns.get("OR_operators", 0) + 1
                if "after:" in dork:
                    keywords_patterns["time_filters"] = keywords_patterns.get("time_filters", 0) + 1
                if "filetype:" in dork:
                    keywords_patterns["filetype"] = keywords_patterns.get("filetype", 0) + 1
                if "-" in dork and not dork.startswith("-"):
                    keywords_patterns["exclusions"] = keywords_patterns.get("exclusions", 0) + 1
            
            print(f"   覆盖的平台数: {len(sites)}")
            print(f"   平台列表: {sorted(sites)}")
            print(f"   关键词策略:")
            for pattern, count in sorted(keywords_patterns.items()):
                print(f"      - {pattern}: {count}个dorks")
            
            print(f"\n   生成的Dorks:")
            for i, dork in enumerate(result.dorks, 1):
                # 简化显示
                display_dork = dork[:75] + "..." if len(dork) > 75 else dork
                print(f"      {i:2d}. {display_dork}")
                
        except Exception as e:
            print(f"❌ 生成失败: {e}")
    
    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)

if __name__ == "__main__":
    test_dorks_generation()
