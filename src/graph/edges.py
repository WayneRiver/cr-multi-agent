"""
src/graph/edges.py — 工作流条件边

LangGraph 的条件边是一个函数：
    输入: state (ReviewState 字典)
    输出: str — 下一个要执行的节点名称

这里定义两条条件边：
    after_cache      — cache_check 之后：命中缓存就 END，否则继续 plan
    after_early_stop — early_stop 之后：格式驳回就 END，否则继续 run_layer2
"""

from src.graph.state import ReviewState
from src.models.review_result import ReviewStatus

# 条件边 1：after_cache — 缓存命中则跳过所有流程
def after_cache(state: ReviewState) -> str:
    """
    缓存检查后的路由判断

    逻辑：
        - cache_hit 为 True  → 直接跳到 END（不经过后续节点）
        - cache_hit 为 False → 继续到 plan 节点

    命中缓存后跳到 aggregate（汇总缓存结果）
    """
    if state.get("cache_hit"):
        return "aggregate"
    return "plan"

# 条件边 2：after_early_stop — 格式严重驳回则终止
def after_early_stop(state: ReviewState) -> str:
    """
    提前终止判断后的路由判断

    逻辑：
        - result 存在且 status=REJECT → 跳到 cache_write（写缓存后结束）
        - 否则 → 继续到 run_layer2

    
    驳回的结果进行缓存，避免下次再跑一遍格式检查
    """
    result = state.get("result")
    if result is not None and result.status == ReviewStatus.REJECT:
        return "cache_write"
    return "run_layer2"
