"""
ProblemValidateTool 测试。

测试题面样例和样例文件验证功能。
"""

import os
import tempfile

import pytest

from autocode_mcp.tools.validation import ProblemValidateTool


class TestCompareOutput:
    """测试输出比较逻辑。"""

    def test_exact_match(self):
        """测试精确匹配。"""
        tool = ProblemValidateTool()
        assert tool._compare_output("hello", "hello", 1e-9) is True
        assert tool._compare_output("hello\nworld", "hello\nworld", 1e-9) is True

    def test_whitespace_insensitive(self):
        """测试空白字符不敏感比较。"""
        tool = ProblemValidateTool()
        # 尾部空白
        assert tool._compare_output("hello  ", "hello", 1e-9) is True
        assert tool._compare_output("hello\n", "hello", 1e-9) is True
        # 每行尾部空白
        assert tool._compare_output("hello  \nworld  ", "hello\nworld", 1e-9) is True

    def test_token_match(self):
        """测试 token 级别比较。"""
        tool = ProblemValidateTool()
        # 多空格压缩
        assert tool._compare_output("1  2  3", "1 2 3", 1e-9) is True
        assert tool._compare_output("1\t2\t3", "1 2 3", 1e-9) is True

    def test_floating_point_match(self):
        """测试浮点数比较。"""
        tool = ProblemValidateTool()
        # 精确匹配
        assert tool._compare_output("1.0 2.0 3.0", "1.0 2.0 3.0", 1e-9) is True
        # 容差内匹配
        assert tool._compare_output("1.000000001", "1.0", 1e-6) is True
        # 超出容差
        assert tool._compare_output("1.1", "1.0", 1e-9) is False

    def test_mismatch(self):
        """测试不匹配情况。"""
        tool = ProblemValidateTool()
        assert tool._compare_output("hello", "world", 1e-9) is False
        assert tool._compare_output("1 2 3", "1 2 4", 1e-9) is False


class TestExtractSamplesFromReadme:
    """测试从 README 提取样例。"""

    def test_chinese_format(self):
        """测试中文格式样例提取。"""
        tool = ProblemValidateTool()
        readme_content = """# 测试题目

**样例输入 1**
```text
5
3 -5 2 -8 4
```

**样例输出 1**
```text
2
```
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(readme_content)
            temp_path = f.name

        try:
            samples = tool._extract_samples_from_readme(temp_path)
            assert len(samples) == 1
            assert samples[0]["input"] == "5\n3 -5 2 -8 4"
            assert samples[0]["expected_output"] == "2"
        finally:
            os.unlink(temp_path)

    def test_english_format(self):
        """测试英文格式样例提取。"""
        tool = ProblemValidateTool()
        readme_content = """# Test Problem

**Sample Input 1**
```text
1 2
```

**Sample Output 1**
```text
3
```
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(readme_content)
            temp_path = f.name

        try:
            samples = tool._extract_samples_from_readme(temp_path)
            assert len(samples) == 1
            assert samples[0]["input"] == "1 2"
            assert samples[0]["expected_output"] == "3"
        finally:
            os.unlink(temp_path)

    def test_multiple_samples(self):
        """测试多个样例提取。"""
        tool = ProblemValidateTool()
        readme_content = """# 测试题目

**样例输入 1**
```text
1
```

**样例输出 1**
```text
1
```

**样例输入 2**
```text
2
```

**样例输出 2**
```text
4
```
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(readme_content)
            temp_path = f.name

        try:
            samples = tool._extract_samples_from_readme(temp_path)
            assert len(samples) == 2
            assert samples[0]["input"] == "1"
            assert samples[1]["input"] == "2"
        finally:
            os.unlink(temp_path)

    def test_no_samples(self):
        """测试无样例情况。"""
        tool = ProblemValidateTool()
        readme_content = """# 测试题目

这是一个没有样例的题目描述。
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(readme_content)
            temp_path = f.name

        try:
            samples = tool._extract_samples_from_readme(temp_path)
            assert len(samples) == 0
        finally:
            os.unlink(temp_path)

    def test_heading_style_samples(self):
        """测试标题样式样例提取（### 样例输入 #k / ### 样例输出 #k）。"""
        tool = ProblemValidateTool()
        readme_content = """# 测试题目

## 样例

### 样例输入 #1

```text
2
1 2
```

### 样例输出 #1

```text
3
```

## 说明

这里只解释代表性样例。
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(readme_content)
            temp_path = f.name

        try:
            samples = tool._extract_samples_from_readme(temp_path)
            assert len(samples) == 1
            assert samples[0]["input"] == "2\n1 2"
            assert samples[0]["expected_output"] == "3"
        finally:
            os.unlink(temp_path)


class TestProblemValidateTool:
    """测试 ProblemValidateTool 工具属性。"""

    def test_tool_name(self):
        """测试工具名称。"""
        tool = ProblemValidateTool()
        assert tool.name == "problem_validate"

    def test_tool_description(self):
        """测试工具描述。"""
        tool = ProblemValidateTool()
        assert "验证" in tool.description or "validate" in tool.description.lower()

    def test_input_schema(self):
        """测试输入 schema。"""
        tool = ProblemValidateTool()
        schema = tool.input_schema
        assert schema["type"] == "object"
        assert "problem_dir" in schema["properties"]
        assert "validate_types" in schema["properties"]
        assert "statement_samples" in schema["properties"]

    @pytest.mark.asyncio
    async def test_interactive_protocol_validation_accepts_transcript(self, tmp_path):
        """交互题 transcript 样例不应被当作普通 stdin/stdout 样例执行。"""
        (tmp_path / "statements").mkdir()
        (tmp_path / "autocode.json").write_text(
            '{"schema_version":"1.0","problem_name":"I","interactive":true}',
            encoding="utf-8",
        )
        (tmp_path / "statements" / "README.md").write_text(
            """# I

## 输入格式

本题为交互题，选手程序不会获得传统静态输入。

## 输出格式

输出 `? x` 查询，输出 `! s` 作为最终答案。每次输出后必须 flush 或刷新。

## 交互协议

交互开始时 judge 等待选手输出。最多允许 20 次查询。交互器返回 -1、0、1。

## 样例

```text
contestant: ? 50
judge: -1
contestant: ! 63
```
""",
            encoding="utf-8",
        )

        result = await ProblemValidateTool().execute(problem_dir=str(tmp_path))

        assert result.success
        assert result.data["interactive_protocol"]["passed"] is True
        assert result.data["statement_samples"]["mode"] == "interactive_protocol"

    @pytest.mark.asyncio
    async def test_interactive_protocol_validation_rejects_missing_protocol(self, tmp_path):
        """交互题题面缺协议时应失败。"""
        (tmp_path / "statements").mkdir()
        (tmp_path / "autocode.json").write_text(
            '{"schema_version":"1.0","problem_name":"I","interactive":true}',
            encoding="utf-8",
        )
        (tmp_path / "statements" / "README.md").write_text("# I\n\n## 样例\n\n无\n", encoding="utf-8")

        result = await ProblemValidateTool().execute(problem_dir=str(tmp_path))

        assert not result.success
        assert result.data["interactive_protocol"]["passed"] is False


class TestExtractSamplesPlainText:
    """测试纯文本格式样例提取（无代码块）。"""

    def test_chinese_plain_text(self):
        """测试中文纯文本格式。"""
        tool = ProblemValidateTool()
        readme_content = """# 测试题目

样例输入：
5
3 -5 2 -8 4

样例输出：
2
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(readme_content)
            temp_path = f.name

        try:
            samples = tool._extract_samples_from_readme(temp_path)
            assert len(samples) == 1
            assert samples[0]["input"] == "5\n3 -5 2 -8 4"
            assert samples[0]["expected_output"] == "2"
        finally:
            os.unlink(temp_path)

    def test_english_plain_text(self):
        """测试英文纯文本格式。"""
        tool = ProblemValidateTool()
        readme_content = """# Test Problem

Sample Input:
1 2

Sample Output:
3
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(readme_content)
            temp_path = f.name

        try:
            samples = tool._extract_samples_from_readme(temp_path)
            assert len(samples) == 1
            assert samples[0]["input"] == "1 2"
            assert samples[0]["expected_output"] == "3"
        finally:
            os.unlink(temp_path)

    def test_plain_text_with_colon_variants(self):
        """测试不同冒号格式。"""
        tool = ProblemValidateTool()
        # 中文冒号
        readme_content = """# 测试题目
样例输入：
1
样例输出：
1
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(readme_content)
            temp_path = f.name

        try:
            samples = tool._extract_samples_from_readme(temp_path)
            assert len(samples) == 1
        finally:
            os.unlink(temp_path)
