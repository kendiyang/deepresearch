"""
缓存层实现 - 支持 Redis 和本地文件两种模式

Cost Control Strategy:
- Key: md5(dork_string) 
- Value: Serper API JSON 响应
- TTL: 24小时 (搜索结果日级时效性)

Usage:
    # 自动模式（优先 Redis，回退到文件缓存）
    cache = SearchCache()
    
    # 强制使用 Redis
    cache = SearchCache(cache_type="redis", redis_url="redis://localhost:6379/0")
    
    # 强制使用文件缓存
    cache = SearchCache(cache_type="file", cache_dir="./cache")
"""

import os
import json
import hashlib
import time
import logging
from typing import Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)


class SearchCache:
    """双模式缓存：Redis (生产) 或 File (开发/降级)"""
    
    def __init__(
        self,
        *,
        cache_type: Optional[str] = None,
        redis_url: Optional[str] = None,
        cache_dir: Optional[str] = None,
        ttl_seconds: int = 86400,  # 24小时
    ):
        """
        Args:
            cache_type: "redis" | "file" | None (自动选择)
            redis_url: Redis 连接字符串，如 "redis://localhost:6379/0"
            cache_dir: 文件缓存目录路径
            ttl_seconds: 缓存过期时间（秒），默认24小时
        """
        self.ttl_seconds = ttl_seconds
        self.cache_type = cache_type
        self._redis_client = None
        self._cache_dir = None
        
        # 自动选择缓存模式
        if cache_type == "redis":
            self._init_redis(redis_url)
        elif cache_type == "file":
            self._init_file_cache(cache_dir)
        else:
            # 自动模式：优先 Redis，失败则降级到文件缓存
            try:
                self._init_redis(redis_url)
                self.cache_type = "redis"
            except Exception as e:
                logger.warning("Redis 初始化失败，降级到文件缓存: %s", e)
                self._init_file_cache(cache_dir)
                self.cache_type = "file"
        
        logger.info("缓存层初始化成功: type=%s, ttl=%ds", self.cache_type, self.ttl_seconds)
    
    def _init_redis(self, redis_url: Optional[str]) -> None:
        """初始化 Redis 客户端"""
        try:
            import redis
        except ImportError:
            raise ImportError(
                "Redis 模式需要安装 redis 库: pip install redis"
            )
        
        url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._redis_client = redis.from_url(url, decode_responses=True)
        # 测试连接
        self._redis_client.ping()
        logger.info("Redis 连接成功: %s", url)
    
    def _init_file_cache(self, cache_dir: Optional[str]) -> None:
        """初始化文件缓存目录"""
        cache_path = cache_dir or os.getenv("CACHE_DIR", "./cache/serper")
        self._cache_dir = Path(cache_path)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info("文件缓存目录: %s", self._cache_dir.absolute())
    
    def _make_cache_key(self, dork: str) -> str:
        """生成缓存键: md5(dork_string)"""
        hash_obj = hashlib.md5(dork.encode("utf-8"))
        return f"serper:dork:{hash_obj.hexdigest()}"
    
    def get(self, dork: str) -> Optional[Dict[str, Any]]:
        """
        从缓存获取搜索结果
        
        Returns:
            Serper API 的 JSON 响应，或 None（缓存未命中）
        """
        cache_key = self._make_cache_key(dork)
        
        try:
            if self.cache_type == "redis":
                return self._get_from_redis(cache_key)
            else:
                return self._get_from_file(cache_key)
        except Exception as e:
            logger.warning("缓存读取失败: key=%s, error=%s", cache_key, e)
            return None
    
    def set(self, dork: str, value: Dict[str, Any]) -> bool:
        """
        写入缓存
        
        Args:
            dork: 搜索关键词
            value: Serper API 返回的完整 JSON
            
        Returns:
            成功返回 True，失败返回 False
        """
        cache_key = self._make_cache_key(dork)
        
        try:
            if self.cache_type == "redis":
                return self._set_to_redis(cache_key, value)
            else:
                return self._set_to_file(cache_key, value)
        except Exception as e:
            logger.warning("缓存写入失败: key=%s, error=%s", cache_key, e)
            return False
    
    def _get_from_redis(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """从 Redis 读取"""
        raw = self._redis_client.get(cache_key)
        if raw is None:
            return None
        return json.loads(raw)
    
    def _set_to_redis(self, cache_key: str, value: Dict[str, Any]) -> bool:
        """写入 Redis（带 TTL）"""
        raw = json.dumps(value, ensure_ascii=False)
        self._redis_client.setex(cache_key, self.ttl_seconds, raw)
        return True
    
    def _get_from_file(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """从文件读取（检查过期时间）"""
        # 提取 hash 作为文件名
        hash_part = cache_key.split(":")[-1]
        cache_file = self._cache_dir / f"{hash_part}.json"
        
        if not cache_file.exists():
            return None
        
        # 检查文件是否过期
        file_mtime = cache_file.stat().st_mtime
        age_seconds = time.time() - file_mtime
        if age_seconds > self.ttl_seconds:
            logger.debug("缓存过期: %s (age=%.1fs)", cache_file.name, age_seconds)
            cache_file.unlink(missing_ok=True)
            return None
        
        # 读取内容
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)
    
    def _set_to_file(self, cache_key: str, value: Dict[str, Any]) -> bool:
        """写入文件"""
        hash_part = cache_key.split(":")[-1]
        cache_file = self._cache_dir / f"{hash_part}.json"
        
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
        return True
    
    def clear(self) -> None:
        """清空所有缓存（仅用于测试）"""
        if self.cache_type == "redis":
            # 删除所有 serper:dork:* 键
            pattern = "serper:dork:*"
            for key in self._redis_client.scan_iter(pattern):
                self._redis_client.delete(key)
            logger.info("Redis 缓存已清空")
        else:
            # 删除所有 .json 文件
            for cache_file in self._cache_dir.glob("*.json"):
                cache_file.unlink()
            logger.info("文件缓存已清空")
    
    def stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        if self.cache_type == "redis":
            pattern = "serper:dork:*"
            keys = list(self._redis_client.scan_iter(pattern))
            return {
                "cache_type": "redis",
                "total_keys": len(keys),
                "ttl_seconds": self.ttl_seconds,
            }
        else:
            files = list(self._cache_dir.glob("*.json"))
            total_size = sum(f.stat().st_size for f in files)
            return {
                "cache_type": "file",
                "total_files": len(files),
                "total_size_mb": round(total_size / 1024 / 1024, 2),
                "cache_dir": str(self._cache_dir.absolute()),
                "ttl_seconds": self.ttl_seconds,
            }


class CacheMetrics:
    """缓存性能监控"""
    
    def __init__(self):
        self.hits = 0
        self.misses = 0
        self.api_calls = 0
        self.cost_saved = 0.0  # 节省的美元
        self.api_cost_per_1k = 1.0  # Serper: $1/1000次
    
    def record_hit(self) -> None:
        """记录缓存命中"""
        self.hits += 1
        self.cost_saved += self.api_cost_per_1k / 1000
    
    def record_miss(self) -> None:
        """记录缓存未命中"""
        self.misses += 1
        self.api_calls += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0.0
        
        return {
            "total_requests": total,
            "cache_hits": self.hits,
            "cache_misses": self.misses,
            "hit_rate_percent": round(hit_rate, 2),
            "api_calls": self.api_calls,
            "cost_saved_usd": round(self.cost_saved, 4),
            "estimated_cost_usd": round(self.api_calls * self.api_cost_per_1k / 1000, 4),
        }
    
    def reset(self) -> None:
        """重置统计"""
        self.hits = 0
        self.misses = 0
        self.api_calls = 0
        self.cost_saved = 0.0
