"""Standalone layout detection, source discovery, and limit inference mixin for ``ProblemAuditTool``."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Literal

from ..utils.communication_runner import (
    is_safe_stem,
    safe_resolve_under_root,
)
from ..utils.problem_layout import (
    PolygonProblemMeta,
    discover_test_pairs,
    discover_wrong_solution_files,
    parse_polygon_problem_xml,
)
from ..workflow.models import (
    AuditGates,
    AutoCodeManifest,
    ProtocolConfig,
    QualityGates,
    SolutionEntry,
)
from .complexity import _strip_cpp_comments_and_strings


class _AuditDiscoveryMixin:
    """Layout detection, artifact discovery, symlink/path guards, and manifest inference."""

    @staticmethod
    def _safe_file(problem_path: Path, rel: str) -> Path | None:
        resolved = safe_resolve_under_root(str(problem_path), rel)
        if resolved is None:
            return None
        p = Path(resolved)
        return p if p.is_file() else None

    @staticmethod
    def _safe_dir(problem_path: Path, rel: str) -> Path | None:
        resolved = safe_resolve_under_root(str(problem_path), rel)
        if resolved is None:
            return None
        p = Path(resolved)
        return p if p.is_dir() else None

    @staticmethod
    def _parse_polygon_xml_safe(problem_path: Path) -> PolygonProblemMeta | None:
        try:
            return parse_polygon_problem_xml(problem_path)
        except (ValueError, OSError):
            return None

    @classmethod
    def _detect_layout(
        cls,
        problem_path: Path,
        requested: Literal["auto", "autocode", "standard_cn", "polygon", "hydro"] = "auto",
    ) -> Literal["autocode", "standard_cn", "polygon", "hydro"]:
        if requested != "auto":
            return requested
        if cls._safe_file(problem_path, ".autocode/manifest.json") is not None:
            return "autocode"
        if cls._safe_dir(problem_path, "testdata") is not None and (
            cls._safe_file(problem_path, "problem.yaml") is not None
            or cls._safe_file(problem_path, "testdata/config.yaml") is not None
            or cls._safe_file(problem_path, "problem_zh.md") is not None
        ):
            return "hydro"
        if (
            cls._safe_dir(problem_path, "data/test") is not None
            or cls._safe_file(problem_path, "solution/std.cpp") is not None
            or cls._safe_file(problem_path, "statement/zh-cn.md") is not None
            or cls._safe_file(problem_path, "statement/zh.md") is not None
        ):
            return "standard_cn"
        return "polygon"

    @classmethod
    def _resolve_tests_dir(
        cls,
        problem_path: Path,
        *,
        standalone: bool = False,
        layout: Literal["auto", "autocode", "standard_cn", "polygon", "hydro"] = "auto",
    ) -> Path | None:
        if not standalone:
            return cls._safe_dir(problem_path, "tests")
        eff_layout = cls._detect_layout(problem_path, layout)
        poly_meta = cls._parse_polygon_xml_safe(problem_path)
        if eff_layout == "polygon" and poly_meta is not None and poly_meta.input_path_pattern:
            pat_parent_rel = Path(poly_meta.input_path_pattern).parent.as_posix()
            pat_dir = (
                cls._safe_dir(problem_path, pat_parent_rel)
                if pat_parent_rel not in ("", ".")
                else problem_path.resolve()
            )
            if pat_dir is not None and discover_test_pairs(
                problem_path,
                tests_dir=pat_dir,
                input_pattern=poly_meta.input_path_pattern,
                answer_pattern=poly_meta.answer_path_pattern,
                test_count=poly_meta.test_count,
                allow_missing_answer=True,
            ):
                return pat_dir
        if eff_layout == "standard_cn":
            candidates = ("data/test", "data", "tests", "testdata")
        elif eff_layout == "hydro":
            candidates = ("testdata", "tests", "data/test", "data")
        else:
            candidates = ("tests", "data/test", "testdata", "data")
        first_existing: Path | None = None
        for rel in candidates:
            cand = cls._safe_dir(problem_path, rel)
            if cand is None:
                continue
            if first_existing is None:
                first_existing = cand
            try:
                if any(p.is_file() and p.suffix == ".in" for p in cand.iterdir()):
                    return cand
                if discover_test_pairs(problem_path, tests_dir=cand, allow_missing_answer=True):
                    return cand
            except OSError:
                continue
        return first_existing

    def _find_main_solution_source(
        self,
        problem_path: Path,
        manifest: AutoCodeManifest,
        *,
        standalone: bool = False,
        layout: Literal["auto", "autocode", "standard_cn", "polygon", "hydro"] = "auto",
    ) -> Path | None:
        for s in manifest.solutions:
            if s.role == "main" and s.path:
                found = self._safe_file(problem_path, s.path)
                if found is not None:
                    return found
        candidates = (
            ("solution/std.cpp", "solutions/sol.cpp", "solutions/std.cpp", "std.cpp", "sol.cpp")
            if layout == "standard_cn"
            else ("solutions/sol.cpp", "solution/std.cpp", "solutions/std.cpp", "sol.cpp", "std.cpp")
        )
        for rel in candidates if standalone else ("solutions/sol.cpp", "sol.cpp"):
            found = self._safe_file(problem_path, rel)
            if found is not None:
                return found
        if standalone:
            for s in manifest.solutions:
                if s.role == "reference" and s.path:
                    found = self._safe_file(problem_path, s.path)
                    if found is not None:
                        return found
            poly_meta = self._parse_polygon_xml_safe(problem_path)
            if poly_meta is not None:
                for ps in sorted(poly_meta.solutions, key=lambda x: 0 if x.role == "main" else 1):
                    if ps.role in ("main", "reference"):
                        found = self._safe_file(problem_path, ps.path)
                        if found is not None:
                            return found
        return None

    def _classify_tpj_source(self, problem_path: Path) -> tuple[Literal["interactive", "spj"], Path] | None:
        """Detect standalone ``tpj.cc`` / ``tpj.cpp`` and classify as interactive or testlib checker."""
        for rel in ("tpj.cc", "tpj.cpp", "files/tpj.cc", "files/tpj.cpp"):
            cand = self._safe_file(problem_path, rel)
            if cand is None:
                continue
            try:
                with cand.open("r", encoding="utf-8", errors="replace") as f:
                    snippet = f.read(262144)
            except OSError:
                continue
            cleaned = _strip_cpp_comments_and_strings(snippet)
            kind: Literal["interactive", "spj"] = (
                "interactive" if re.search(r"\bregisterInteraction\s*\(", cleaned) else "spj"
            )
            return kind, cand
        return None

    def _find_validator_source(self, problem_path: Path, *, standalone: bool = False) -> Path | None:
        candidates = (
            (
                "files/val.cpp",
                "validator/val.cpp",
                "validator/validator.cpp",
                "files/validator.cpp",
                "data/validator.cpp",
                "data/val.cpp",
                "testdata/val.cpp",
                "validator.cpp",
                "val.cpp",
            )
            if standalone
            else ("files/val.cpp",)
        )
        for rel in candidates:
            found = self._safe_file(problem_path, rel)
            if found is not None:
                return found
        if standalone:
            poly_meta = self._parse_polygon_xml_safe(problem_path)
            if poly_meta is not None:
                for val_rel in poly_meta.validator_paths:
                    found = self._safe_file(problem_path, val_rel)
                    if found is not None:
                        return found
        return None

    def _find_checker_source(self, problem_path: Path, *, standalone: bool = False) -> Path | None:
        candidates = (
            (
                "files/checker.cpp",
                "checker/checker.cpp",
                "checker/chk.cpp",
                "testdata/checker.cpp",
                "chk.cpp",
            )
            if standalone
            else ("files/checker.cpp",)
        )
        for rel in candidates:
            found = self._safe_file(problem_path, rel)
            if found is not None:
                return found
        if standalone:
            poly_meta = self._parse_polygon_xml_safe(problem_path)
            if poly_meta is not None and poly_meta.checker_path:
                found = self._safe_file(problem_path, poly_meta.checker_path)
                if found is not None:
                    return found
            tpj = self._classify_tpj_source(problem_path)
            if tpj is not None and tpj[0] == "spj":
                return tpj[1]
        return None

    def _find_interactor_source(self, problem_path: Path, *, standalone: bool = False) -> Path | None:
        candidates = (
            (
                "files/interactor.cpp",
                "interactor/interactor.cpp",
                "interactor.cpp",
                "testdata/interactor.cpp",
            )
            if standalone
            else ("files/interactor.cpp",)
        )
        for rel in candidates:
            found = self._safe_file(problem_path, rel)
            if found is not None:
                return found
        if standalone:
            poly_meta = self._parse_polygon_xml_safe(problem_path)
            if (
                poly_meta is not None
                and poly_meta.interactor_path
                and not poly_meta.communication_mode
            ):
                found = self._safe_file(problem_path, poly_meta.interactor_path)
                if found is not None:
                    return found
            tpj = self._classify_tpj_source(problem_path)
            if tpj is not None and tpj[0] == "interactive":
                return tpj[1]
        return None

    def _find_generator_source(self, problem_path: Path, *, standalone: bool = False) -> Path | None:
        candidates = (
            (
                "files/gen.cpp",
                "files/generator.cpp",
                "data/generator.cpp",
                "data/gen.cpp",
                "generator/generator.cpp",
                "generator/gen.cpp",
                "gen/generator.cpp",
                "gen/gen.cpp",
                "testdata/generator.cpp",
                "testdata/gen.cpp",
                "generator.cpp",
                "gen.cpp",
            )
            if standalone
            else ("files/gen.cpp",)
        )
        for rel in candidates:
            found = self._safe_file(problem_path, rel)
            if found is not None:
                return found
        if standalone:
            poly_meta = self._parse_polygon_xml_safe(problem_path)
            if poly_meta is not None:
                for gen_rel in poly_meta.generator_paths:
                    found = self._safe_file(problem_path, gen_rel)
                    if found is not None:
                        return found
        return None

    def _parse_statement_limits(
        self,
        problem_path: Path,
        statement_path: str | None = None,
        *,
        layout: Literal["auto", "autocode", "standard_cn", "polygon", "hydro"] = "auto",
    ) -> tuple[int | None, int | None]:
        """Parse time (ms) and memory (MB) limits from the first 40 lines of statement Markdown."""
        stmt_candidates = (
            (
                "statement/zh-cn.md",
                "statement/zh.md",
                "statement/README.md",
                "statements/README.md",
                "statements/zh-cn.md",
                "problem_zh.md",
                "statement.md",
                "README.md",
            )
            if layout == "standard_cn"
            else (
                "statements/README.md",
                "statement/zh-cn.md",
                "statement/zh.md",
                "statement/README.md",
                "statements/zh-cn.md",
                "problem_zh.md",
                "statement.md",
                "README.md",
            )
        )
        ordered_candidates = (
            (statement_path, *stmt_candidates) if statement_path else stmt_candidates
        )
        stmt_file: Path | None = None
        for rel in ordered_candidates:
            if not rel:
                continue
            cand = self._safe_file(problem_path, rel)
            if cand is not None:
                stmt_file = cand
                break
        if stmt_file is None:
            return None, None
        try:
            if stmt_file.stat().st_size > 2 * 1024 * 1024:
                return None, None
            header = "\n".join(
                stmt_file.read_text(encoding="utf-8", errors="replace").splitlines()[:40]
            )
        except OSError:
            return None, None

        tl_ms: int | None = None
        ml_mb: int | None = None

        time_patterns = (
            r"时间限制[*`\s]*[：:]\s*[*`\s]*(\d+(?:\.\d+)?)\s*(ms|毫秒|s|sec|seconds?|秒)",
            r"time\s*limit[*`\s]*[：:]?\s*[*`\s]*(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|sec|seconds?)\b",
        )
        for pat in time_patterns:
            m_t = re.search(pat, header, re.IGNORECASE)
            if not m_t:
                continue
            try:
                val = float(m_t.group(1))
            except ValueError:
                continue
            unit = m_t.group(2).lower()
            parsed_ms = int(round(val * 1000.0)) if unit in {"s", "sec", "second", "seconds", "秒"} else int(round(val))
            if 100 <= parsed_ms <= 60_000:
                tl_ms = parsed_ms
                break

        mem_patterns = (
            r"(?:内存|空间)限制[*`\s]*[：:]\s*[*`\s]*(\d+(?:\.\d+)?)\s*(mib|mb|兆字节|兆|gib|gb)",
            r"memory\s*limit[*`\s]*[：:]?\s*[*`\s]*(\d+(?:\.\d+)?)\s*(mib|mb|gib|gb)\b",
        )
        for pat in mem_patterns:
            m_m = re.search(pat, header, re.IGNORECASE)
            if not m_m:
                continue
            try:
                val_m = float(m_m.group(1))
            except ValueError:
                continue
            unit_m = m_m.group(2).lower()
            parsed_mb = int(round(val_m * 1024.0)) if unit_m in {"gib", "gb"} else int(round(val_m))
            if 16 <= parsed_mb <= 16_384:
                ml_mb = parsed_mb
                break

        return tl_ms, ml_mb

    def _infer_standalone_limits(
        self,
        problem_path: Path,
        *,
        layout: Literal["auto", "autocode", "standard_cn", "polygon", "hydro"] = "auto",
        statement_path: str | None = None,
    ) -> tuple[int, int]:
        """Infer (time_limit_ms, memory_limit_mb) from config files, falling back to statement header."""
        import yaml

        from ..utils.exporters import _parse_hydro_mem_mb, _parse_hydro_time_ms

        tl_ms = 2000
        ml_mb = 256

        def _try_polygon(rel: str = "problem.xml") -> tuple[int | None, int | None] | None:
            xml_f = self._safe_file(problem_path, rel)
            if xml_f is None:
                return None
            poly_root = xml_f.parent
            poly = self._parse_polygon_xml_safe(poly_root)
            if poly is None:
                return None
            res_tl: int | None = None
            res_ml: int | None = None
            if poly.time_limit_ms is not None and 100 <= poly.time_limit_ms <= 60_000:
                res_tl = poly.time_limit_ms
            if poly.memory_limit_mb is not None and 16 <= poly.memory_limit_mb <= 16_384:
                res_ml = poly.memory_limit_mb
            return (res_tl, res_ml) if (res_tl is not None or res_ml is not None) else None

        def _try_hydro(rel: str = "testdata/config.yaml") -> tuple[int | None, int | None] | None:
            h_cfg = self._safe_file(problem_path, rel)
            if h_cfg is None:
                return None
            try:
                if h_cfg.stat().st_size <= 2 * 1024 * 1024:
                    parsed = yaml.safe_load(h_cfg.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(parsed, dict):
                        res_tl: int | None = None
                        res_ml: int | None = None
                        ptl = _parse_hydro_time_ms(parsed.get("time"))
                        pml = _parse_hydro_mem_mb(parsed.get("memory"))
                        if ptl and 100 <= ptl <= 60_000:
                            res_tl = ptl
                        if pml and 16 <= pml <= 16_384:
                            res_ml = pml
                        if res_tl is not None or res_ml is not None:
                            return res_tl, res_ml
            except (yaml.YAMLError, OSError, ValueError):
                pass
            return None

        def _try_qoj(rel: str = "problem.conf") -> tuple[int | None, int | None] | None:
            q_cfg = self._safe_file(problem_path, rel)
            if q_cfg is None:
                return None
            try:
                if q_cfg.stat().st_size <= 2 * 1024 * 1024:
                    kv: dict[str, str] = {}
                    for ln in q_cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                        parts = ln.strip().split(" ", 1)
                        if len(parts) == 2:
                            kv[parts[0]] = parts[1].strip()
                    res_tl: int | None = None
                    res_ml: int | None = None
                    if kv.get("time_limit_ms", "").isdigit() and len(kv["time_limit_ms"]) <= 9:
                        v = int(kv["time_limit_ms"])
                        if 100 <= v <= 60_000:
                            res_tl = v
                    elif "time_limit" in kv and len(kv["time_limit"]) <= 12:
                        try:
                            fv = float(kv["time_limit"]) * 1000.0
                            if math.isfinite(fv) and 100 <= fv <= 60_000:
                                res_tl = int(round(fv))
                        except ValueError:
                            pass
                    if kv.get("memory_limit", "").isdigit() and len(kv["memory_limit"]) <= 6:
                        v = int(kv["memory_limit"])
                        if 16 <= v <= 16_384:
                            res_ml = v
                    if res_tl is not None or res_ml is not None:
                        return res_tl, res_ml
            except OSError:
                pass
            return None

        order = (
            (_try_hydro, _try_polygon, _try_qoj)
            if layout == "hydro"
            else (_try_qoj, _try_hydro, _try_polygon)
            if layout == "standard_cn"
            else (_try_polygon, _try_hydro, _try_qoj)
        )
        found_tl: int | None = None
        found_ml: int | None = None
        for fn in order:
            got = fn()
            if got is not None:
                found_tl = got[0] if got[0] is not None else found_tl
                found_ml = got[1] if got[1] is not None else found_ml
                break
        if found_tl is None and found_ml is None:
            for rel_fn, export_rel in (
                (_try_qoj, "exports/qoj/problem.conf"),
                (_try_hydro, "exports/hydro/testdata/config.yaml"),
                (_try_polygon, "exports/polygon/problem.xml"),
            ):
                got = rel_fn(export_rel)
                if got is not None:
                    found_tl = got[0] if got[0] is not None else found_tl
                    found_ml = got[1] if got[1] is not None else found_ml
                    break
        if found_tl is None or found_ml is None:
            stmt_tl, stmt_ml = self._parse_statement_limits(
                problem_path, statement_path=statement_path, layout=layout
            )
            if found_tl is None:
                found_tl = stmt_tl
            if found_ml is None:
                found_ml = stmt_ml
        return (
            found_tl if found_tl is not None else tl_ms,
            found_ml if found_ml is not None else ml_mb,
        )

    def _infer_standalone_manifest(
        self,
        problem_path: Path,
        *,
        layout: Literal["auto", "autocode", "standard_cn", "polygon", "hydro"] = "auto",
    ) -> AutoCodeManifest:
        """Infer an in-memory manifest for external problem packages without `.autocode/manifest.json`."""
        problem_name = problem_path.resolve().name or "standalone"
        problem_type: Literal["standard", "spj", "interactive", "communication"] = "standard"
        interactive = False
        special_judge = False
        stress_comparison: Literal["exact", "checker"] = "exact"
        oracle_mode: Literal["brute", "reference_solutions", "interactor_only"] = "brute"
        protocol: ProtocolConfig | None = None

        def _first_existing(candidates: tuple[str, ...]) -> str | None:
            for rel in candidates:
                if self._safe_file(problem_path, rel) is not None:
                    return rel
            return None

        poly_meta = self._parse_polygon_xml_safe(problem_path)

        if _first_existing(
            ("files/interactor.cpp", "interactor/interactor.cpp", "interactor.cpp", "testdata/interactor.cpp")
        ):
            problem_type = "interactive"
            interactive = True
            oracle_mode = "interactor_only"
        elif (
            grader_rel := _first_existing(
                ("files/grader.cpp", "grader.cpp", "testdata/grader.cpp")
            )
            or (
                poly_meta.grader_path
                if (
                    poly_meta is not None
                    and poly_meta.communication_mode == "grader_linked"
                    and poly_meta.grader_path
                    and self._safe_file(problem_path, poly_meta.grader_path) is not None
                )
                else None
            )
        ):
            problem_type = "communication"
            oracle_mode = "interactor_only"
            header_file = _first_existing(
                ("files/problem.h", "problem.h", "testdata/problem.h")
            ) or (
                poly_meta.header_path
                if (
                    poly_meta is not None
                    and poly_meta.header_path
                    and self._safe_file(problem_path, poly_meta.header_path) is not None
                )
                else None
            )
            protocol = ProtocolConfig(
                mode="grader_linked",
                grader_file=grader_rel,
                header_file=header_file,
            )
        elif (
            comm_rel := _first_existing(
                ("files/communicator.cpp", "communicator.cpp", "testdata/communicator.cpp")
            )
            or (
                poly_meta.interactor_path
                if (
                    poly_meta is not None
                    and poly_meta.communication_mode in ("run_twice", "multi_process")
                    and poly_meta.interactor_path
                    and self._safe_file(problem_path, poly_meta.interactor_path) is not None
                )
                else None
            )
        ):
            problem_type = "communication"
            oracle_mode = "interactor_only"
            comm_mode_val: Literal["run_twice", "multi_process"] = (
                "multi_process"
                if (poly_meta is not None and poly_meta.communication_mode == "multi_process")
                else "run_twice"
            )
            protocol = ProtocolConfig(
                mode=comm_mode_val,
                communicator_file=comm_rel,
            )
        elif (
            poly_meta is not None
            and poly_meta.interactor_path
            and not poly_meta.communication_mode
            and self._safe_file(problem_path, poly_meta.interactor_path) is not None
        ):
            problem_type = "interactive"
            interactive = True
            oracle_mode = "interactor_only"
        elif _first_existing(
            ("files/checker.cpp", "checker/checker.cpp", "checker/chk.cpp", "chk.cpp", "testdata/checker.cpp")
        ) or (
            poly_meta is not None
            and poly_meta.checker_path
            and self._safe_file(problem_path, poly_meta.checker_path) is not None
        ):
            problem_type = "spj"
            special_judge = True
            stress_comparison = "checker"
        elif (tpj_info := self._classify_tpj_source(problem_path)) is not None:
            if tpj_info[0] == "interactive":
                problem_type = "interactive"
                interactive = True
                oracle_mode = "interactor_only"
            else:
                problem_type = "spj"
                special_judge = True
                stress_comparison = "checker"

        stmt_candidates = (
            (
                "statement/zh-cn.md",
                "statement/zh.md",
                "statement/README.md",
                "statements/README.md",
                "statements/zh-cn.md",
                "problem_zh.md",
                "statement.md",
                "README.md",
            )
            if layout == "standard_cn"
            else (
                "statements/README.md",
                "statement/zh-cn.md",
                "statement/zh.md",
                "statement/README.md",
                "statements/zh-cn.md",
                "problem_zh.md",
                "statement.md",
                "README.md",
            )
        )
        statement_path = _first_existing(stmt_candidates)
        if statement_path is None and poly_meta is not None:
            for sp in poly_meta.statement_paths:
                if sp.lower().endswith((".md", ".tex", ".txt")) and self._safe_file(problem_path, sp) is not None:
                    statement_path = sp
                    break
        statement_path = statement_path or "statements/README.md"
        tutorial_path = (
            _first_existing(
                (
                    "statements/tutorial.md",
                    "statement/tutorial.md",
                    "solution/tutorial.md",
                    "tutorial.md",
                    "editorial.md",
                )
            )
            or "statements/tutorial.md"
        )

        solutions: list[SolutionEntry] = []
        seen_paths: set[str] = set()
        seen_names: set[str] = set()

        def _unique_name(raw_stem: str) -> str:
            base = raw_stem if is_safe_stem(raw_stem) else "solution"
            if base not in seen_names:
                return base
            idx = 2
            while f"{base}_{idx}" in seen_names:
                idx += 1
            return f"{base}_{idx}"

        has_poly_main = False
        if poly_meta is not None:
            for ps in poly_meta.solutions:
                if self._safe_file(problem_path, ps.path) is None:
                    continue
                if ps.path in seen_paths:
                    continue
                seen_paths.add(ps.path)
                if ps.role == "main" and not has_poly_main and "sol" not in seen_names:
                    sol_name = "sol"
                    has_poly_main = True
                else:
                    sol_name = _unique_name(Path(ps.path).stem)
                seen_names.add(sol_name)
                solutions.append(
                    SolutionEntry(
                        name=sol_name,
                        path=ps.path,
                        role=ps.role,
                        language=ps.language,
                        expected=ps.expected,
                        expected_verdict=ps.expected_verdict,
                    )
                )

        if not has_poly_main:
            main_candidates = (
                ("solution/std.cpp", "solutions/sol.cpp", "solutions/std.cpp", "std.cpp", "sol.cpp")
                if layout == "standard_cn"
                else ("solutions/sol.cpp", "solution/std.cpp", "solutions/std.cpp", "sol.cpp", "std.cpp")
            )
            main_rel = _first_existing(main_candidates)
            if main_rel is not None and main_rel not in seen_paths:
                seen_paths.add(main_rel)
                main_name = _unique_name("sol")
                seen_names.add(main_name)
                solutions.insert(
                    0,
                    SolutionEntry(name=main_name, path=main_rel, role="main", expected="pass"),
                )

        brute_rel = _first_existing(
            (
                "solutions/brute.cpp",
                "solution/brute.cpp",
                "solution/brute_force.cpp",
                "solutions/brute_force.cpp",
                "brute.cpp",
                "brute_force.cpp",
            )
        )
        if brute_rel is not None and brute_rel not in seen_paths:
            seen_paths.add(brute_rel)
            brute_name = _unique_name("brute")
            seen_names.add(brute_name)
            solutions.append(
                SolutionEntry(name=brute_name, path=brute_rel, role="brute", expected="pass")
            )

        seen_names.update({"sol", "brute"})
        real_root = problem_path.resolve()
        for wrong_file in discover_wrong_solution_files(problem_path):
            try:
                rel_wrong = wrong_file.resolve().relative_to(real_root).as_posix()
            except ValueError:
                continue
            if rel_wrong in seen_paths:
                continue
            seen_paths.add(rel_wrong)
            wrong_name = _unique_name(wrong_file.stem)
            seen_names.add(wrong_name)
            solutions.append(
                SolutionEntry(
                    name=wrong_name,
                    path=rel_wrong,
                    role="wrong",
                    expected="fail",
                )
            )

        tl_ms, ml_mb = self._infer_standalone_limits(
            problem_path, layout=layout, statement_path=statement_path
        )
        return AutoCodeManifest(
            problem_name=problem_name,
            problem_type=problem_type,
            interactive=interactive,
            special_judge=special_judge,
            stress_comparison=stress_comparison,
            oracle_mode=oracle_mode,
            protocol=protocol,
            time_limit_ms=tl_ms,
            memory_limit_mb=ml_mb,
            statement_path=statement_path,
            tutorial_path=tutorial_path,
            solutions=solutions,
            quality_gates=QualityGates(
                require_stress_passed=False,
                require_validation_passed=False,
                require_tests_verified=False,
                require_limit_semantics=False,
                require_validator_check=False,
                require_wrong_solution_kill=False,
            ),
            audit_gates=AuditGates(
                require_full_audit=False,
                require_validator_self_test=False,
                require_checker_self_test=False,
                require_interactor_self_test=False,
                require_statement_consistency=False,
            ),
        )

    @staticmethod
    def _find_escaped_entries(problem_path: Path, directory: Path) -> list[str]:
        real_root = str(problem_path.resolve())
        escaped: list[str] = []
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return ["<unreadable_directory>"]
        for entry in entries:
            try:
                entry.resolve().relative_to(real_root)
            except (ValueError, RuntimeError, OSError):
                escaped.append(entry.name)
        return escaped
