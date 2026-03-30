"""
Prompts 模块测试。
"""
from autocode_mcp import prompts


def test_list_prompts():
    """测试列出提示词。"""
    prompt_list = prompts.list_prompts()
    assert isinstance(prompt_list, list)
    assert len(prompt_list) == 6
    assert "full_pipeline" in prompt_list
    assert "validator" in prompt_list
    assert "generator" in prompt_list


def test_get_prompt_exists():
    """测试获取存在的提示词。"""
    content = prompts.get_prompt("validator")
    assert content != ""
    assert "Validator" in content
    assert "Algorithm 1" in content


def test_get_prompt_full_pipeline():
    """测试获取完整流程提示词。"""
    content = prompts.get_prompt("full_pipeline")
    assert content != ""
    assert "出题流程" in content


def test_get_prompt_generator():
    """测试获取 Generator 提示词。"""
    content = prompts.get_prompt("generator")
    assert content != ""
    assert "Generator" in content
    assert "Algorithm 2" in content


def test_get_prompt_checker():
    """测试获取 Checker 提示词。"""
    content = prompts.get_prompt("checker")
    assert content != ""
    assert "Checker" in content
    assert "Algorithm 3" in content


def test_get_prompt_interactor():
    """测试获取 Interactor 提示词。"""
    content = prompts.get_prompt("interactor")
    assert content != ""
    assert "Interactor" in content
    assert "Algorithm 4" in content


def test_get_prompt_not_exists():
    """测试获取不存在的提示词。"""
    content = prompts.get_prompt("nonexistent")
    assert content == ""
