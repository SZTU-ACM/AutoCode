import math
import os
import subprocess
from typing import Any

from .ratio_analyzer import normalize_complexity_expression


class MultiScaleSampler:
    @classmethod
    def compute_scale_points(cls, n_max: int, complexity_type: str = "O(n)") -> list[int]:
        if n_max <= 0:
            return []
        if n_max == 1:
            return [1]

        norm_comp = normalize_complexity_expression(complexity_type)

        if norm_comp in ("O(2^n)", "O(n!)") or n_max <= 30:
            count = min(5, n_max)
            start = max(1, n_max - count + 1)
            points = list(range(start, n_max + 1))
            return sorted(set(points))

        if n_max < 1000:
            ratios = [0.20, 0.40, 0.60, 0.80, 1.00]
            raw_points = [max(1, int(math.floor(n_max * r))) for r in ratios]
            return sorted(set(raw_points))

        ratios = [0.02, 0.05, 0.10, 0.30, 1.00]
        raw_points = [max(10, int(math.floor(n_max * r))) for r in ratios]
        raw_points[-1] = n_max
        points_set = sorted(set(raw_points))
        return points_set

    @classmethod
    def format_generator_command(
        cls,
        generator_exe: str,
        n: int,
        seed: int,
        template: str | None = None,
        extra_vars: dict[str, Any] | None = None,
    ) -> list[str]:
        vars_map: dict[str, Any] = {
            "n": n,
            "seed": seed,
            "n_min": n,
            "n_max": n,
            "t_min": 1,
            "t_max": 1,
        }
        if extra_vars:
            vars_map.update(extra_vars)

        if template:
            formatted = template.format(**vars_map)
            parts = formatted.strip().split()
            return [generator_exe] + parts

        # 默认匹配既有 testlib 生成器标准格式: gen <seed> <type> <n_min> <n_max> <t_min> <t_max>
        return [
            generator_exe,
            str(seed),
            "random",
            str(vars_map.get("n_min", n)),
            str(n),
            str(vars_map.get("t_min", 1)),
            str(vars_map.get("t_max", 1)),
        ]

    @classmethod
    def generate_scale_input_file(
        cls,
        cmd: list[str],
        output_file_path: str,
        timeout_sec: float = 10.0,
    ) -> bool:
        os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
        with open(output_file_path, "w", encoding="utf-8", newline="\n") as out_f:
            proc = subprocess.run(
                cmd,
                stdout=out_f,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Generator execution failed with exit code {proc.returncode}: {proc.stderr.strip()}"
                )
        return True
