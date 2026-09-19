import os
import sys

from autocode_mcp.utils.scale_sampler import MultiScaleSampler


def test_compute_scale_points_large() -> None:
    pts = MultiScaleSampler.compute_scale_points(100000, "O(n log n)")
    assert len(pts) == 5
    assert pts == [2000, 5000, 10000, 30000, 100000]
    for i in range(len(pts) - 1):
        assert pts[i] < pts[i + 1]


def test_compute_scale_points_medium() -> None:
    pts = MultiScaleSampler.compute_scale_points(500, "O(n^3)")
    assert len(pts) >= 4
    assert pts[-1] == 500
    for i in range(len(pts) - 1):
        assert pts[i] < pts[i + 1]


def test_compute_scale_points_small_exponential() -> None:
    pts = MultiScaleSampler.compute_scale_points(20, "O(2^n)")
    assert len(pts) == 5
    assert pts == [16, 17, 18, 19, 20]
    for i in range(len(pts) - 1):
        assert pts[i] < pts[i + 1]


def test_compute_scale_points_tiny_boundary() -> None:
    pts = MultiScaleSampler.compute_scale_points(3, "O(2^n)")
    assert pts == [1, 2, 3]
    assert MultiScaleSampler.compute_scale_points(0, "O(n)") == []
    assert MultiScaleSampler.compute_scale_points(1, "O(n)") == [1]


def test_format_generator_command_default() -> None:
    cmd = MultiScaleSampler.format_generator_command("gen.exe", 5000, 42)
    assert cmd == ["gen.exe", "42", "random", "5000", "5000", "1", "1"]


def test_format_generator_command_template() -> None:
    cmd = MultiScaleSampler.format_generator_command(
        "gen.exe",
        5000,
        42,
        template="-n {n} --seed {seed} -m {m}",
        extra_vars={"m": 10000},
    )
    assert cmd == ["gen.exe", "-n", "5000", "--seed", "42", "-m", "10000"]


def test_generate_scale_input_file(tmp_path: os.PathLike[str]) -> None:
    out_file = os.path.join(str(tmp_path), "test_scale.in")
    cmd = [sys.executable, "-c", "import sys; print(' '.join(sys.argv[1:]))", "10", "20", "30"]
    MultiScaleSampler.generate_scale_input_file(cmd, out_file)
    assert os.path.isfile(out_file)
    with open(out_file, encoding="utf-8") as f:
        content = f.read().strip()
    assert content == "10 20 30"


def test_generate_scale_input_file_failure(tmp_path: os.PathLike[str]) -> None:
    import pytest
    out_file = os.path.join(str(tmp_path), "test_fail.in")
    cmd = [sys.executable, "-c", "import sys; sys.exit(1)"]
    with pytest.raises(RuntimeError) as exc_info:
        MultiScaleSampler.generate_scale_input_file(cmd, out_file)
    assert "exit code 1" in str(exc_info.value)
