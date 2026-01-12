
from abc import ABC, abstractmethod
from typing import Any

class DataPreprocessor(ABC):
	"""
	统一数据预处理接口，所有预处理器需实现 preprocess 方法。
	"""
	@abstractmethod
	def preprocess(self, data: Any) -> Any:
		"""
		对输入数据进行预处理，返回处理后的数据。
		:param data: 原始输入数据（可为文本、dict、DataFrame等）
		:return: 预处理后的数据
		"""
		pass
