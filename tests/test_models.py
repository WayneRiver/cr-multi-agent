"""
tests/test_models.py - 数据模型的单元测试

覆盖 Finding、ReviewInput、ReviewResult 三个核心模型的：
1. 成功创建 —— 正常输入应该构造出正确的实例
2. 校验失败 —— 非法输入应该抛出 ValidationError
3. 边界值   —— 临界条件需要正常工作
"""

import pytest
from pydantic import ValidationError
from src.models.finding import Finding, Severity, FindingType
from src.models.review_input import ReviewInput, Language
from src.models.review_result import ReviewResult, ReviewStatus


# ============================================================
# 一、Finding 模型测试
# ============================================================

class TestFinding:
    """Finding 模型的单元测试"""

    # ---------- 1. 成功创建 ----------

    def test_create_valid_finding(self):
        """正常创建一条 Finding：所有字段应有正确的值"""
        finding = Finding(
            severity=Severity.HIGH,
            type=FindingType.SECURITY,
            file="src/app.py",
            line_start=42,
            line_end=42,
            title="检测到 SQL 注入风险",
            description="直接拼接用户输入构造 SQL 查询",
            suggestion="使用参数化查询代替字符串拼接",
        )

        # 验证自动生成字段
        assert isinstance(finding.id, str)
        assert len(finding.id) == 36  # UUID 格式：xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

        # 验证分类字段
        assert finding.severity == Severity.HIGH
        assert finding.type == FindingType.SECURITY

        # 验证定位字段
        assert finding.file == "src/app.py"
        assert finding.line_start == 42
        assert finding.line_end == 42

        # 验证描述字段
        assert finding.title == "检测到 SQL 注入风险"
        assert finding.description == "直接拼接用户输入构造 SQL 查询"
        assert finding.suggestion == "使用参数化查询代替字符串拼接"

    def test_uuid_is_unique(self):
        """每条 Finding 应该有唯一的 UUID"""
        finding1 = Finding(
            severity=Severity.MEDIUM,
            type=FindingType.STYLE,
            file="a.py",
            line_start=1,
            line_end=1,
            title="问题1",
        )
        finding2 = Finding(
            severity=Severity.MEDIUM,
            type=FindingType.STYLE,
            file="a.py",
            line_start=1,
            line_end=1,
            title="问题2",
        )

        assert finding1.id != finding2.id

    def test_minimal_finding(self):
        """只提供必填字段时，可选字段应该有默认值"""
        finding = Finding(
            severity=Severity.LOW,
            type=FindingType.LOGIC,
            file="main.py",
            line_start=10,
            line_end=15,
            title="变量未使用",
        )

        # 可选字段的默认值
        assert finding.description == ""
        assert finding.suggestion == ""

    # ---------- 2. 校验失败 ----------

    def test_missing_required_fields(self):
        """缺少必填字段时应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            Finding(
                # 缺少 severity
                type=FindingType.STYLE,
                file="a.py",
                line_start=1,
                line_end=1,
                title="问题",
            )

    def test_line_start_must_be_positive(self):
        """line_start 不能为 0 或负数（ge=1 约束）"""
        with pytest.raises(ValidationError):
            Finding(
                severity=Severity.INFO,
                type=FindingType.STYLE,
                file="a.py",
                line_start=0,  # 非法：行号从 1 开始
                line_end=1,
                title="问题",
            )

        with pytest.raises(ValidationError):
            Finding(
                severity=Severity.INFO,
                type=FindingType.STYLE,
                file="a.py",
                line_start=-1,  # 非法：负数
                line_end=1,
                title="问题",
            )

    def test_line_end_must_be_positive(self):
        """line_end 不能为 0 或负数"""
        with pytest.raises(ValidationError):
            Finding(
                severity=Severity.INFO,
                type=FindingType.STYLE,
                file="a.py",
                line_start=1,
                line_end=0,  # 非法
                title="问题",
            )

    def test_line_end_cannot_be_less_than_line_start(self):
        """跨字段校验：line_end 不能小于 line_start"""
        with pytest.raises(ValidationError) as exc_info:
            Finding(
                severity=Severity.INFO,
                type=FindingType.STYLE,
                file="a.py",
                line_start=10,
                line_end=5,  # 非法：结束行 < 起始行
                title="问题",
            )

        # 验证报错信息包含字段名
        assert "line_end" in str(exc_info.value) or "line_start" in str(exc_info.value)

    def test_invalid_severity_value(self):
        """传入非法的 severity 字符串应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            Finding(
                severity="fatal",  # 不在枚举中
                type=FindingType.STYLE,
                file="a.py",
                line_start=1,
                line_end=1,
                title="问题",
            )

    def test_invalid_type_value(self):
        """传入非法的 type 字符串应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            Finding(
                severity=Severity.INFO,
                type="performance",  # 不在枚举中
                file="a.py",
                line_start=1,
                line_end=1,
                title="问题",
            )

    # ---------- 3. 边界值 ----------

    def test_single_line_finding(self):
        """单行问题：line_start 等于 line_end 应该正常工作"""
        finding = Finding(
            severity=Severity.INFO,
            type=FindingType.STYLE,
            file="a.py",
            line_start=1,
            line_end=1,  # 和 line_start 相等，是合法边界值
            title="单行问题",
        )
        assert finding.line_start == finding.line_end

    def test_multi_line_finding(self):
        """跨行问题：line_end 大于 line_start 应该正常工作"""
        finding = Finding(
            severity=Severity.INFO,
            type=FindingType.LOGIC,
            file="a.py",
            line_start=10,
            line_end=50,  # 跨 40 行的问题
            title="函数过长",
        )
        assert finding.line_end > finding.line_start

    def test_very_long_title(self):
        """很长的标题应该也能正常保存"""
        long_title = "这是一个" + "非常" * 200 + "长的问题标题"
        finding = Finding(
            severity=Severity.INFO,
            type=FindingType.STYLE,
            file="a.py",
            line_start=1,
            line_end=1,
            title=long_title,
        )
        assert finding.title == long_title

    def test_empty_optional_fields(self):
        """description 和 suggestion 可以传空字符串"""
        finding = Finding(
            severity=Severity.INFO,
            type=FindingType.STYLE,
            file="a.py",
            line_start=1,
            line_end=1,
            title="问题",
            description="",   # 显式传空字符串
            suggestion="",    # 显式传空字符串
        )
        assert finding.description == ""
        assert finding.suggestion == ""


# ============================================================
# 二、ReviewInput 模型测试
# ============================================================

class TestReviewInput:
    """ReviewInput 模型的单元测试"""

    # ---------- 1. 成功创建 ----------

    def test_create_with_all_fields(self):
        """所有字段都提供时，正常创建"""
        review_input = ReviewInput(
            code="def add(a, b): return a + b",
            language=Language.PYTHON,
            pr_description="添加加法函数",
            commit_hash="abc123def456",
        )

        assert review_input.code == "def add(a, b): return a + b"
        assert review_input.language == Language.PYTHON
        assert review_input.pr_description == "添加加法函数"
        assert review_input.commit_hash == "abc123def456"

    def test_create_with_minimal_fields(self):
        """只提供必填字段 code + language，可选字段用默认值"""
        review_input = ReviewInput(
            code="def foo(): pass",
            language=Language.GO,
        )

        # 可选字段的默认值
        assert review_input.pr_description == ""
        assert review_input.commit_hash == ""

    def test_create_with_language_from_string(self):
        """language 字段支持字符串构造（Pydantic 自动转为枚举）"""
        review_input = ReviewInput(
            code="console.log('hello')",
            language="javascript",  # 用字符串，不用 Language.JAVASCRIPT
        )
        assert review_input.language == Language.JAVASCRIPT

    def test_all_supported_languages(self):
        """验证 5 种语言都能正常创建"""
        languages = ["python", "java", "go", "javascript", "typescript"]
        for lang in languages:
            review_input = ReviewInput(
                code="// some code",
                language=lang,
            )
            assert review_input.language.value == lang

    # ---------- 2. 校验失败 ----------

    def test_code_is_required(self):
        """缺少 code 字段应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            ReviewInput(language=Language.PYTHON)

    def test_code_cannot_be_empty(self):
        """空字符串 code 应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            ReviewInput(
                code="",
                language=Language.PYTHON,
            )

    def test_code_cannot_be_whitespace_only(self):
        """纯空白字符的 code 应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            ReviewInput(
                code="   \n\t  ",  # 只有空格、换行、制表符
                language=Language.PYTHON,
            )

    def test_language_is_required(self):
        """缺少 language 字段应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            ReviewInput(code="def foo(): pass")

    def test_invalid_language_value(self):
        """非法的 language 值应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            ReviewInput(
                code="def foo(): pass",
                language="rust",  # 不支持的语言
            )

    # ---------- 3. 边界值 ----------

    def test_minimal_valid_code(self):
        """最短的有效代码：单个字符"""
        review_input = ReviewInput(
            code="x",  # 一个字符
            language=Language.PYTHON,
        )
        assert len(review_input.code) == 1

    def test_very_long_code(self):
        """很长的代码内容也应该正常处理"""
        long_code = "def foo():\n    pass\n" * 1000  # 约 20000 字符
        review_input = ReviewInput(
            code=long_code,
            language=Language.PYTHON,
        )
        assert len(review_input.code) == len(long_code)

    def test_empty_pr_description_is_allowed(self):
        """pr_description 可以为空字符串"""
        review_input = ReviewInput(
            code="def foo(): pass",
            language=Language.PYTHON,
            pr_description="",
        )
        assert review_input.pr_description == ""


# ============================================================
# 三、ReviewResult 模型测试
# ============================================================

class TestReviewResult:
    """ReviewResult 模型的单元测试"""

    # ---------- 1. 成功创建 ----------

    def test_create_empty_result(self):
        """评审结果为空时（无 Finding、无降级），正常创建"""
        result = ReviewResult()

        assert result.status == ReviewStatus.PASS
        assert result.findings == []
        assert result.skipped_agents == []
        assert result.degraded is False
        assert result.total_duration_ms == 0

    def test_create_with_findings(self):
        """评审结果包含 Finding 时，正常创建"""
        finding = Finding(
            severity=Severity.HIGH,
            type=FindingType.SECURITY,
            file="app.py",
            line_start=5,
            line_end=5,
            title="SQL 注入",
        )

        result = ReviewResult(
            status=ReviewStatus.REJECT,
            findings=[finding],
            skipped_agents=["style_checker"],
            degraded=True,
            total_duration_ms=5432,
        )

        assert result.status == ReviewStatus.REJECT
        assert len(result.findings) == 1
        assert "style_checker" in result.skipped_agents
        assert result.degraded is True
        assert result.total_duration_ms == 5432

    # ---------- 便捷方法测试 ----------

    def test_is_passed_returns_true_for_pass(self):
        """status 为 PASS 时，is_passed() 返回 True"""
        result = ReviewResult(status=ReviewStatus.PASS)
        assert result.is_passed() is True

    def test_is_passed_returns_false_for_reject(self):
        """status 为 REJECT 时，is_passed() 返回 False"""
        result = ReviewResult(status=ReviewStatus.REJECT)
        assert result.is_passed() is False

    def test_has_critical_findings_detects_critical(self):
        """包含 critical 级别的 Finding，has_critical_findings() 返回 True"""
        critical_finding = Finding(
            severity=Severity.CRITICAL,
            type=FindingType.SECURITY,
            file="app.py",
            line_start=1,
            line_end=1,
            title="严重漏洞",
        )
        result = ReviewResult(findings=[critical_finding])
        assert result.has_critical_findings() is True

    def test_has_critical_findings_ignores_non_critical(self):
        """不包含 critical 级别的 Finding，has_critical_findings() 返回 False"""
        info_finding = Finding(
            severity=Severity.INFO,
            type=FindingType.STYLE,
            file="app.py",
            line_start=1,
            line_end=1,
            title="小建议",
        )
        result = ReviewResult(findings=[info_finding])
        assert result.has_critical_findings() is False

    def test_has_critical_findings_empty_list(self):
        """空 Finding 列表时，has_critical_findings() 返回 False"""
        result = ReviewResult(findings=[])
        assert result.has_critical_findings() is False

    def test_finding_count_property(self):
        """finding_count 属性返回 Finding 总数"""
        result = ReviewResult(findings=[])
        assert result.findings_count == 0

        # 添加 3 条 Finding
        for i in range(3):
            result.findings.append(
                Finding(
                    severity=Severity.INFO,
                    type=FindingType.STYLE,
                    file=f"file_{i}.py",
                    line_start=i + 1,
                    line_end=i + 1,
                    title=f"问题 {i}",
                )
            )
        assert result.findings_count == 3

    # ---------- 2. 校验失败 ----------

    def test_total_duration_ms_cannot_be_negative(self):
        """total_duration_ms 不能为负数（ge=0 约束）"""
        with pytest.raises(ValidationError):
            ReviewResult(total_duration_ms=-1)

    # ---------- 3. 边界值 ----------

    def test_default_status_is_pass(self):
        """不传 status 时，默认为 PASS"""
        result = ReviewResult()
        assert result.status == ReviewStatus.PASS

    def test_default_findings_is_empty_list(self):
        """不传 findings 时，默认是空列表"""
        result = ReviewResult()
        assert result.findings == []

    def test_result_with_all_status_types(self):
        """验证三种 status 都能正常设置"""
        for status in ReviewStatus:
            result = ReviewResult(status=status)
            assert result.status == status

    def test_multiple_findings_count(self):
        """多条 Finding 的计数正确"""
        findings = [
            Finding(
                severity=Severity.MEDIUM,
                type=FindingType.STYLE,
                file="a.py",
                line_start=i,
                line_end=i,
                title=f"问题 {i}",
            )
            for i in range(1, 11)  # 10 条 Finding
        ]
        result = ReviewResult(findings=findings)
        assert result.findings_count == 10
