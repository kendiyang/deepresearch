from typing import TypedDict, List, Annotated
import operator

class ResearchState(TypedDict):
    task_id: str
    original_query: str
    current_hypothesis: str             # 当前的假设（如：用户不是讨厌产品，是讨厌设置过程）
    search_queries: List[str]           # 下一步要搜什么
    gathered_data: List[str]            # 原始数据摘要
    insights: List[str]                 # 提炼出的洞察
    iteration_count: int                # 递归计数器
    depth_score: float                  # 深度打分 (0.0 - 10.0)
    is_satisfied: bool                  # 是否满足退出条件
    final_report: str