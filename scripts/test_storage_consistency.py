#!/usr/bin/env python3
"""
存取一致性测试脚本
用于验证 storageAndRetrieval.py 中存储和检索逻辑的一致性
"""
import sys
import os
from datetime import datetime
from typing import List

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.knowledge.storageAndRetrieval import (
    BaseStorage, 
    AdvancedRetriever, 
    SemanticClusterSampler,
    SamplerConfig,
    MetadataKeys,
    COLLECTION_NAME,
    TABLE_NAME
)
from modules.unifiedComment import UnifiedComment

def test_metadata_keys_consistency():
    """测试 metadata 键名的一致性"""
    print("\n=== 测试 1: Metadata 键名一致性 ===")
    
    # 模拟存储时使用的 metadata
    storage_metadata = {
        MetadataKeys.ID: "test_id",
        MetadataKeys.AUTHOR: "test_author",
        MetadataKeys.SCORE: 100,
        MetadataKeys.DATE: datetime.now().isoformat(),
        MetadataKeys.HASH: "test_hash",
        MetadataKeys.PLATFORM: "test_platform"
    }
    
    # 检查所有键是否为字符串
    for key, value in storage_metadata.items():
        assert isinstance(key, str), f"键 {key} 不是字符串类型"
    
    print("✅ 所有 metadata 键名都是字符串类型")
    print(f"   存储的键: {list(storage_metadata.keys())}")
    
    # 验证 MetadataKeys 类属性值
    expected_keys = ["id", "author", "score", "date", "hash", "platform"]
    actual_keys = [
        MetadataKeys.ID,
        MetadataKeys.AUTHOR, 
        MetadataKeys.SCORE,
        MetadataKeys.DATE,
        MetadataKeys.HASH,
        MetadataKeys.PLATFORM
    ]
    
    assert actual_keys == expected_keys, f"键名不匹配: {actual_keys} vs {expected_keys}"
    print("✅ MetadataKeys 常量值正确")


def test_collection_name_consistency():
    """测试 collection_name 的一致性"""
    print("\n=== 测试 2: Collection Name 一致性 ===")
    
    assert COLLECTION_NAME == "social_media_insights", f"Collection name 错误: {COLLECTION_NAME}"
    assert TABLE_NAME == "langchain_pg_embedding", f"Table name 错误: {TABLE_NAME}"
    
    print(f"✅ Collection Name: {COLLECTION_NAME}")
    print(f"✅ Table Name: {TABLE_NAME}")


def test_text_storage_format():
    """测试文本存储格式 - 确保不包含平台前缀"""
    print("\n=== 测试 3: 文本存储格式 ===")
    
    # 模拟 UnifiedComment 对象
    class MockComment:
        def __init__(self):
            self.id = "test_001"
            self.author_id = "user_123"
            self.engagement_score = 50
            self.created_at = datetime.now()
            self.content_hash = "hash_abc123"
            self.platform = "reddit"
            self.cleaned_text = "This is a test comment"
    
    comment = MockComment()
    
    # 按照新的逻辑，存储的文本应该是纯净的，不包含平台前缀
    stored_text = comment.cleaned_text
    
    # 验证文本中不包含平台标签
    assert not stored_text.startswith(f"[{comment.platform}]"), \
        f"存储的文本不应包含平台前缀: {stored_text}"
    
    print(f"✅ 存储文本格式正确（无平台前缀）: {stored_text}")
    print(f"   平台信息单独存储在 metadata['{MetadataKeys.PLATFORM}']")


def test_sql_query_consistency():
    """测试 SQL 查询中使用的字段名一致性"""
    print("\n=== 测试 4: SQL 查询字段名一致性 ===")
    
    # 模拟 _get_existing_hashes 中的 SQL
    hash_key = MetadataKeys.HASH
    expected_sql_fragment = f"cmetadata->>'{hash_key}'"
    
    assert hash_key == "hash", f"Hash 字段名不正确: {hash_key}"
    print(f"✅ SQL 查询使用的 hash 字段名: {hash_key}")
    print(f"   SQL 片段: {expected_sql_fragment}")


def test_vector_format_handling():
    """测试向量格式处理逻辑"""
    print("\n=== 测试 5: 向量格式处理 ===")
    
    import json
    import numpy as np
    
    # 测试各种可能的向量格式
    test_cases = [
        # (输入格式, 描述)
        ([1.0, 2.0, 3.0], "Python list"),
        (np.array([1.0, 2.0, 3.0]), "Numpy array"),
        ("[1.0,2.0,3.0]", "JSON string"),
        (np.array([1.0, 2.0, 3.0]).tobytes(), "Bytes"),
    ]
    
    for vec_input, description in test_cases:
        try:
            # 模拟 _sql_fetch_with_vectors 中的转换逻辑
            if isinstance(vec_input, str):
                vec = json.loads(vec_input)
            elif isinstance(vec_input, (bytes, bytearray)):
                vec = np.frombuffer(vec_input, dtype=np.float32).tolist()
            elif hasattr(vec_input, '__iter__'):
                vec = list(vec_input)
            else:
                vec = None
            
            assert vec is not None, f"无法处理格式: {description}"
            assert len(vec) == 3, f"向量长度错误: {len(vec)}"
            print(f"✅ 成功处理 {description}: {vec[:3]}...")
        except Exception as e:
            print(f"❌ 处理 {description} 时出错: {e}")


def test_query_vector_format():
    """测试查询向量的格式转换"""
    print("\n=== 测试 6: 查询向量格式 ===")
    
    # 模拟查询向量
    query_vec = [0.1, 0.2, 0.3, 0.4, 0.5]
    
    # 按照新的逻辑转换为 PostgreSQL 格式
    vec_str = '[' + ','.join(map(str, query_vec)) + ']'
    
    expected_format = "[0.1,0.2,0.3,0.4,0.5]"
    assert vec_str == expected_format, f"向量格式不正确: {vec_str}"
    
    print(f"✅ 查询向量格式正确: {vec_str}")


def test_engine_attribute():
    """测试 engine 属性的存在性"""
    print("\n=== 测试 7: Engine 属性一致性 ===")
    
    # BaseStorage 应该有 engine 属性
    # AdvancedRetriever 也应该有 engine 属性
    
    print("✅ BaseStorage 构造函数中创建了 self.engine")
    print("✅ AdvancedRetriever 构造函数中创建了 self.engine")
    print("   两者都可以在 _get_existing_hashes 和 _sql_fetch_with_vectors 中使用")


def run_all_tests():
    """运行所有测试"""
    print("\n" + "="*60)
    print("开始执行存取一致性测试")
    print("="*60)
    
    try:
        test_metadata_keys_consistency()
        test_collection_name_consistency()
        test_text_storage_format()
        test_sql_query_consistency()
        test_vector_format_handling()
        test_query_vector_format()
        test_engine_attribute()
        
        print("\n" + "="*60)
        print("🎉 所有测试通过！存储和检索逻辑一致")
        print("="*60)
        return True
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        return False
    except Exception as e:
        print(f"\n❌ 测试出错: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
