import os
from functools import lru_cache
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '../.env'))

class GlobalConfig:
    """全局配置，自动从环境变量加载。"""
    def __init__(self):
        self.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
        self.OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://chrisapivip.com/v1")
        self.ENV = os.getenv("ENV", "dev")
        self.DEBUG = os.getenv("DEBUG", "0") == "1"
        self.SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
        
@lru_cache()
def get_config() -> GlobalConfig:
    """获取全局唯一配置实例。"""
    return GlobalConfig()

config = get_config()  # 确保配置已加载
