"""
Resources 模块测试。
"""
from autocode_mcp import TEMPLATES_DIR, resources


def test_templates_dir_exists():
    """测试 templates 目录存在。"""
    import os
    assert os.path.exists(TEMPLATES_DIR)


def test_list_templates():
    """测试列出模板。"""
    templates = resources.list_templates()
    assert isinstance(templates, list)
    assert "testlib.h" in templates


def test_get_template_path_exists():
    """测试获取存在的模板路径。"""
    path = resources.get_template_path("testlib.h")
    assert path is not None
    import os
    assert os.path.exists(path)


def test_get_template_path_not_exists():
    """测试获取不存在的模板路径。"""
    path = resources.get_template_path("nonexistent.cpp")
    assert path is None


def test_get_problem_resource_path():
    """测试获取题目资源路径。"""
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建一个 statements 目录和 README.md
        statements_dir = os.path.join(tmpdir, "statements")
        os.makedirs(statements_dir)
        readme_path = os.path.join(statements_dir, "README.md")
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write("# Test Problem")

        path = resources.get_problem_resource_path(tmpdir, "statement")
        assert path is not None
        assert path == readme_path


def test_get_problem_resource_path_not_exists():
    """测试获取不存在的题目资源路径。"""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        path = resources.get_problem_resource_path(tmpdir, "statement")
        assert path is None
