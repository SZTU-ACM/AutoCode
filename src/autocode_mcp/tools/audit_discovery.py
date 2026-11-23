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
            tpj = self._classify_tpj_source(problem_path)
            if tpj is not None and tpj[0] == "interactive":
                return tpj[1]
        return None

    def _infer_standalone_limits(
        self,
        problem_path: Path,
        *,
        layout: Literal["auto", "autocode", "standard_cn", "polygon", "hydro"] = "auto",
    ) -> tuple[int, int]:
        """Infer (time_limit_ms, memory_limit_mb) from problem.xml / config.yaml / problem.conf if present."""
        import xml.etree.ElementTree as ET

        import yaml

        from ..utils.exporters import _parse_hydro_mem_mb, _parse_hydro_time_ms

        tl_ms = 2000
        ml_mb = 256

        def _try_polygon(rel: str = "problem.xml") -> tuple[int, int] | None:
            xml_f = self._safe_file(problem_path, rel)
            if xml_f is None:
                return None
            try:
                if xml_f.stat().st_size <= 5 * 1024 * 1024:
                    raw = xml_f.read_text(encoding="utf-8", errors="replace")
                    if "<!DOCTYPE" not in raw.upper() and "<!ENTITY" not in raw.upper():
                        root = ET.fromstring(raw)
                        tl_n = root.find(".//judging/testset/time-limit")
                        ml_n = root.find(".//judging/testset/memory-limit")
                        res_tl, res_ml = tl_ms, ml_mb
                        found_any = False
                        if tl_n is not None and tl_n.text and tl_n.text.strip().isdigit():
                            v = int(tl_n.text.strip())
                            if 100 <= v <= 60_000:
                                res_tl = v
                                found_any = True
                        if ml_n is not None and ml_n.text and ml_n.text.strip().isdigit():
                            raw_bytes = int(ml_n.text.strip())
                            if raw_bytes % 1_000_000 == 0 and raw_bytes % (1024 * 1024) != 0:
                                v = raw_bytes // 1_000_000
                            else:
                                v = raw_bytes // (1024 * 1024)
                            if 16 <= v <= 16_384:
                                res_ml = v
                                found_any = True
                        if found_any:
                            return res_tl, res_ml
            except (ET.ParseError, OSError, ValueError):
                pass
            return None

        def _try_hydro(rel: str = "testdata/config.yaml") -> tuple[int, int] | None:
            h_cfg = self._safe_file(problem_path, rel)
            if h_cfg is None:
                return None
            try:
                if h_cfg.stat().st_size <= 2 * 1024 * 1024:
                    parsed = yaml.safe_load(h_cfg.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(parsed, dict):
                        res_tl, res_ml = tl_ms, ml_mb
                        found_any = False
                        ptl = _parse_hydro_time_ms(parsed.get("time"))
                        pml = _parse_hydro_mem_mb(parsed.get("memory"))
                        if ptl and 100 <= ptl <= 60_000:
                            res_tl = ptl
                            found_any = True
                        if pml and 16 <= pml <= 16_384:
                            res_ml = pml
                            found_any = True
                        if found_any:
                            return res_tl, res_ml
            except (yaml.YAMLError, OSError, ValueError):
                pass
            return None

        def _try_qoj(rel: str = "problem.conf") -> tuple[int, int] | None:
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
                    res_tl, res_ml = tl_ms, ml_mb
                    found_any = False
                    if kv.get("time_limit_ms", "").isdigit() and len(kv["time_limit_ms"]) <= 9:
                        v = int(kv["time_limit_ms"])
                        if 100 <= v <= 60_000:
                            res_tl = v
                            found_any = True
                    elif "time_limit" in kv and len(kv["time_limit"]) <= 12:
                        try:
                            fv = float(kv["time_limit"]) * 1000.0
                            if math.isfinite(fv) and 100 <= fv <= 60_000:
                                res_tl = int(round(fv))
                                found_any = True
                        except ValueError:
                            pass
                    if kv.get("memory_limit", "").isdigit() and len(kv["memory_limit"]) <= 6:
                        v = int(kv["memory_limit"])
                        if 16 <= v <= 16_384:
                            res_ml = v
                            found_any = True
                    if found_any:
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
        for fn in order:
            got = fn()
            if got is not None:
                return got
        for rel_fn, export_rel in (
            (_try_qoj, "exports/qoj/problem.conf"),
            (_try_hydro, "exports/hydro/testdata/config.yaml"),
            (_try_polygon, "exports/polygon/problem.xml"),
        ):
            got = rel_fn(export_rel)
            if got is not None:
                return got
        return tl_ms, ml_mb

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
        ):
            problem_type = "communication"
            oracle_mode = "interactor_only"
            header_file = _first_existing(
                ("files/problem.h", "problem.h", "testdata/problem.h")
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
        ):
            problem_type = "communication"
            oracle_mode = "interactor_only"
            protocol = ProtocolConfig(
                mode="run_twice",
                communicator_file=comm_rel,
            )
        elif _first_existing(
            ("files/checker.cpp", "checker/checker.cpp", "checker/chk.cpp", "chk.cpp", "testdata/checker.cpp")
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
        statement_path = _first_existing(stmt_candidates) or "statements/README.md"
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
        main_candidates = (
            ("solution/std.cpp", "solutions/sol.cpp", "solutions/std.cpp", "std.cpp", "sol.cpp")
            if layout == "standard_cn"
            else ("solutions/sol.cpp", "solution/std.cpp", "solutions/std.cpp", "sol.cpp", "std.cpp")
        )
        main_rel = _first_existing(main_candidates)
        if main_rel is not None:
            solutions.append(
                SolutionEntry(name="sol", path=main_rel, role="main", expected="pass")
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
        if brute_rel is not None:
            solutions.append(
                SolutionEntry(name="brute", path=brute_rel, role="brute", expected="pass")
            )
        seen_wrong_names: set[str] = {"sol", "brute"}
        for sol_dir_rel, patterns in (
            ("solutions", ("wrong_*.cpp", "wa_*.cpp")),
            ("solution", ("wrong_*.cpp", "wa_*.cpp")),
            ("solutions/wrong", ("*.cpp",)),
            ("solution/wrong", ("*.cpp",)),
        ):
            sols_dir = self._safe_dir(problem_path, sol_dir_rel)
            if sols_dir is None:
                continue
            for pat in patterns:
                for wrong_file in sorted(sols_dir.glob(pat)):
                    if (
                        is_safe_stem(wrong_file.stem)
                        and wrong_file.stem not in seen_wrong_names
                        and self._safe_file(problem_path, f"{sol_dir_rel}/{wrong_file.name}") is not None
                    ):
                        seen_wrong_names.add(wrong_file.stem)
                        solutions.append(
                            SolutionEntry(
                                name=wrong_file.stem,
                                path=f"{sol_dir_rel}/{wrong_file.name}",
                                role="wrong",
                                expected="fail",
                            )
                        )

        tl_ms, ml_mb = self._infer_standalone_limits(problem_path, layout=layout)
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
