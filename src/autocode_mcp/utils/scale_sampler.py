import math
import os
import re
import shlex
import subprocess
from typing import Any

from .ratio_analyzer import normalize_complexity_expression

_SIMPLE_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class MultiScaleSampler:
    _EXPONENTIAL_COMPLEXITIES = frozenset(
        {"O(2^n)", "O(n 2^n)", "O(n^2 2^n)", "O(3^n)", "O(n!)"}
    )

    @classmethod
    def compute_scale_points(
        cls,
        n_max: int,
        complexity_type: str = "O(n)",
        *,
        scale_mode: str = "auto",
        dominant_var: str = "n",
    ) -> list[int]:
        if n_max <= 0:
            return []
        if n_max == 1:
            return [1]

        norm_comp = normalize_complexity_expression(complexity_type, dominant_var=dominant_var)

        if scale_mode == "log":
            if n_max <= 30:
                count = min(5, n_max)
                start = max(1, n_max - count + 1)
                return sorted(set(range(start, n_max + 1)))
            # Geometric progression across [2, n_max] so log2(n) is evenly spaced
            log_min = max(1.0, math.log2(max(2, min(16, n_max))))
            log_max = math.log2(n_max)
            if log_max <= log_min + 1e-6:
                return sorted({max(1, n_max // 2), n_max})
            pts = [
                max(2, min(n_max, int(round(2.0 ** (log_min + (log_max - log_min) * k / 4.0)))))
                for k in range(5)
            ]
            pts[-1] = n_max
            return sorted(set(pts))

        if scale_mode == "exponential":
            if n_max <= 8:
                count = min(5, n_max)
                start = max(1, n_max - count + 1)
                return sorted(set(range(start, n_max + 1)))
            step = max(1, min(2, n_max // 8))
            pts = [max(1, n_max - step * (4 - k)) for k in range(5)]
            pts[-1] = n_max
            return sorted(set(pts))

        if scale_mode == "linear":
            if n_max <= 5:
                return list(range(1, n_max + 1))
            ratios = [0.20, 0.40, 0.60, 0.80, 1.00]
            raw_points = [max(1, int(math.floor(n_max * r))) for r in ratios]
            raw_points[-1] = n_max
            return sorted(set(raw_points))

        if norm_comp in cls._EXPONENTIAL_COMPLEXITIES or n_max <= 30:
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
        dominant_var: str = "n",
    ) -> list[str]:
        vars_map: dict[str, Any] = {
            "n": n,
            "value": n,
            "seed": seed,
            "type": "2",
            "n_min": n,
            "n_max": n,
            "t_min": 1,
            "t_max": 1,
        }
        dom = (dominant_var or "n").strip() or "n"
        if dom.lower() in ("t", "t_max"):
            vars_map["t_min"] = n
            vars_map["t_max"] = n
        vars_map[dom] = n
        if dom.lower() not in ("seed", "type"):
            vars_map[dom.lower()] = n

        if extra_vars:
            vars_map.update(extra_vars)
            # Keep the scaled value bound to the active dominant variable & value placeholder
            vars_map["value"] = n
            vars_map[dom] = n
            if dom.lower() not in ("seed", "type"):
                vars_map[dom.lower()] = n

        if template:
            if "\n" in template or "\r" in template or "\x00" in template:
                raise ValueError("generator_args_template must be a single-line string without NUL/newlines")

            def _replace(match: re.Match[str]) -> str:
                key = match.group(1)
                if key not in vars_map:
                    raise ValueError(f"Unknown template placeholder {{{key}}}")
                return str(vars_map[key])

            formatted = _SIMPLE_PLACEHOLDER_RE.sub(_replace, template)
            if "{" in formatted or "}" in formatted:
                raise ValueError(
                    "generator_args_template only allows simple {identifier} placeholders without format specs or attribute access"
                )
            parts = shlex.split(formatted.strip(), posix=(os.name != "nt"))
            return [generator_exe] + parts

        # 默认匹配既有 testlib 生成器标准格式: gen <seed> <type> <n_min> <n_max> <t_min> <t_max>
        # 其中 type 约定为 1=tiny, 2=random, 3=extreme, 4=tle
        return [
            generator_exe,
            str(vars_map.get("seed", seed)),
            str(vars_map.get("type", "2")),
            str(vars_map.get("n_min", n)),
            str(vars_map.get("n_max", n)),
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
        out_dir = os.path.dirname(output_file_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        resolved_cmd = list(cmd)
        if resolved_cmd and os.path.exists(resolved_cmd[0]):
            resolved_cmd[0] = os.path.abspath(resolved_cmd[0])
        with open(output_file_path, "w", encoding="utf-8", newline="\n") as out_f:
            proc = subprocess.run(
                resolved_cmd,
                stdout=out_f,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Generator execution failed with exit code {proc.returncode}: {proc.stderr.strip()}"
                )
        return True
