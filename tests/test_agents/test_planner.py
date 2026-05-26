"""
tests/test_agents/test_planner.py - Planner 单元测试

测试 Planner 双层决策的第一层（规则判断）。
不需要 mock LLM，因为 Planner(llm=None) 只走规则逻辑。
"""

from src.agents.planner import Planner
from src.models.review_input import ReviewInput


def test_python_all_four_agents():
    """Python → 全部4个 Agent"""
    planner = Planner()
    result = planner.decide(ReviewInput(code="x", language="python"))
    assert result == ["style", "security", "logic", "readability"]


def test_java_all_four_agents():
    """Java → 全部4个 Agent"""
    planner = Planner()
    result = planner.decide(ReviewInput(code="x", language="java"))
    assert result == ["style", "security", "logic", "readability"]


def test_go_no_security():
    """Go → 3个（无 security）"""
    planner = Planner()
    result = planner.decide(ReviewInput(code="x", language="go"))
    assert result == ["style", "logic", "readability"]
    assert "security" not in result


def test_javascript_no_logic():
    """JavaScript → 3个（无 logic）"""
    planner = Planner()
    result = planner.decide(ReviewInput(code="x", language="javascript"))
    assert result == ["style", "security", "readability"]
    assert "logic" not in result


def test_typescript_no_logic():
    """TypeScript → 3个（无 logic）"""
    planner = Planner()
    result = planner.decide(ReviewInput(code="x", language="typescript"))
    assert result == ["style", "security", "readability"]
    assert "logic" not in result


def test_pr_with_format_keyword_reduces_agents():
    """PR描述含'格式化' → 只保留 style + readability"""
    planner = Planner()
    result = planner.decide(
        ReviewInput(code="x", language="python", pr_description="格式化代码")
    )
    assert result == ["style", "readability"]


def test_pr_with_comment_keyword_reduces_agents():
    """PR描述含'注释修改' → 只保留 style + readability"""
    planner = Planner()
    result = planner.decide(
        ReviewInput(code="x", language="go", pr_description="修改注释")
    )
    assert result == ["style", "readability"]


def test_needs_llm_database_triggers():
    """PR描述含'数据库' → needs_llm 返回 True"""
    planner = Planner()
    result = planner._needs_llm(
        ReviewInput(code="x", language="python", pr_description="重构数据库连接池")
    )
    assert result is True


def test_needs_llm_concurrency_triggers():
    """PR描述含'多线程' → needs_llm 返回 True"""
    planner = Planner()
    result = planner._needs_llm(
        ReviewInput(code="x", language="python", pr_description="修复多线程死锁")
    )
    assert result is True


def test_needs_llm_no_trigger():
    """普通PR描述 → needs_llm 返回 False"""
    planner = Planner()
    result = planner._needs_llm(
        ReviewInput(code="x", language="python", pr_description="修改变量名")
    )
    assert result is False


def test_needs_llm_empty_description():
    """空PR描述 → needs_llm 返回 False"""
    planner = Planner()
    result = planner._needs_llm(
        ReviewInput(code="x", language="python")
    )
    assert result is False

