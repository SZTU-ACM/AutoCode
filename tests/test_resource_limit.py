import tempfile
from pathlib import Path

import yaml

from autocode_mcp.utils.resource_limit import (
    ProblemConfig,
    ResourceLimit,
    get_available_memory_mb,
    get_resource_limit,
    load_problem_config,
)


def test_resource_limit_defaults():
    limit = ResourceLimit()
    assert limit.timeout_sec == 30
    assert limit.memory_mb == 256


def test_resource_limit_custom():
    limit = ResourceLimit(timeout_sec=60, memory_mb=512)
    assert limit.timeout_sec == 60
    assert limit.memory_mb == 512


def test_get_available_memory_mb():
    mem = get_available_memory_mb()
    assert isinstance(mem, int)
    assert mem > 0


def test_problem_config_defaults():
    config = ProblemConfig()
    assert config.time_limit is None
    assert config.memory_limit is None


def test_problem_config_custom():
    config = ProblemConfig(time_limit=60, memory_limit=512)
    assert config.time_limit == 60
    assert config.memory_limit == 512


def test_load_problem_config_exists():
    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = Path(tmpdir)
        config_path = problem_dir / "problem.yaml"
        config_data = {"time_limit": 120, "memory_limit": 1024}
        config_path.write_text(yaml.dump(config_data), encoding="utf-8")

        config = load_problem_config(problem_dir)

        assert config.time_limit == 120
        assert config.memory_limit == 1024


def test_load_problem_config_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = Path(tmpdir)

        config = load_problem_config(problem_dir)

        assert config.time_limit is None
        assert config.memory_limit is None


def test_get_resource_limit_sol_defaults():
    with tempfile.TemporaryDirectory() as tmpdir:
        limit = get_resource_limit(tmpdir, "sol")
        assert limit.timeout_sec == 2
        assert limit.memory_mb == 256


def test_get_resource_limit_brute_defaults():
    with tempfile.TemporaryDirectory() as tmpdir:
        available = get_available_memory_mb()
        limit = get_resource_limit(tmpdir, "brute")
        assert limit.timeout_sec == 60
        assert limit.memory_mb == available


def test_get_resource_limit_with_config():
    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = Path(tmpdir)
        config_path = problem_dir / "problem.yaml"
        config_data = {"time_limit": 5, "memory_limit": 512}
        config_path.write_text(yaml.dump(config_data), encoding="utf-8")

        limit = get_resource_limit(str(problem_dir), "sol")
        assert limit.timeout_sec == 5
        assert limit.memory_mb == 512


def test_get_resource_limit_with_params():
    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = Path(tmpdir)
        config_path = problem_dir / "problem.yaml"
        config_data = {"time_limit": 5, "memory_limit": 512}
        config_path.write_text(yaml.dump(config_data), encoding="utf-8")

        limit = get_resource_limit(str(problem_dir), "sol", timeout=10, memory_mb=1024)
        assert limit.timeout_sec == 10
        assert limit.memory_mb == 1024


def test_get_resource_limit_brute_ignores_config():
    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = Path(tmpdir)
        config_path = problem_dir / "problem.yaml"
        config_data = {"time_limit": 5, "memory_limit": 512}
        config_path.write_text(yaml.dump(config_data), encoding="utf-8")

        available = get_available_memory_mb()
        limit = get_resource_limit(str(problem_dir), "brute")
        assert limit.timeout_sec == 60
        assert limit.memory_mb == available
