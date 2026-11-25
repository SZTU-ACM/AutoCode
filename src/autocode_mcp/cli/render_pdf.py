"""Convert problem statements (Markdown) to Typst markup and compile PDF via `typst`."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..utils.communication_runner import safe_resolve_under_root
from ..workflow import load_manifest

TYPST_INSTALL_INSTRUCTIONS = (
    "Install Typst CLI to compile PDFs:\n"
    "  - macOS (Homebrew): brew install typst\n"
    "  - Windows (winget): winget install Typst.Typst\n"
    "  - Cargo (all OS):   cargo install typst-cli\n"
    "  - Releases:         https://github.com/typst/typst/releases\n"
    "Or pass --emit-typ to generate the .typ source file without compiling."
)

_TYPST_ESCAPE_RE = re.compile(r"([\\#$*_@<>\[\]~`])")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_OL_RE = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_HR_RE = re.compile(r"^(?:[-*_]\s*){3,}$")
_FENCE_OPEN_RE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")

_LATEX_WORD_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (r"\\Longleftrightarrow(?![A-Za-z])", "<==>"),
    (r"\\Leftrightarrow(?![A-Za-z])", "<=>"),
    (r"\\Rightarrow(?![A-Za-z])", "=>"),
    (r"\\Leftarrow(?![A-Za-z])", "<="),
    (r"\\leftrightarrow(?![A-Za-z])", "<->"),
    (r"\\rightarrow(?![A-Za-z])", "->"),
    (r"\\leftarrow(?![A-Za-z])", "<-"),
    (r"\\to(?![A-Za-z])", "->"),
    (r"\\leq(?![A-Za-z])", "<="),
    (r"\\le(?![A-Za-z])", "<="),
    (r"\\geq(?![A-Za-z])", ">="),
    (r"\\ge(?![A-Za-z])", ">="),
    (r"\\neq(?![A-Za-z])", "!="),
    (r"\\ne(?![A-Za-z])", "!="),
    (r"\\times(?![A-Za-z])", "times"),
    (r"\\cdot(?![A-Za-z])", "dot"),
    (r"\\cdots(?![A-Za-z])", "dots.c"),
    (r"\\ldots(?![A-Za-z])", "dots"),
    (r"\\dots(?![A-Za-z])", "dots"),
    (r"\\infty(?![A-Za-z])", "infinity"),
    (r"\\sum(?![A-Za-z])", "sum"),
    (r"\\prod(?![A-Za-z])", "product"),
    (r"\\bmod(?![A-Za-z])", "mod"),
    (r"\\notin(?![A-Za-z])", "in.not"),
    (r"\\in(?![A-Za-z])", "in"),
    (r"\\subseteq(?![A-Za-z])", "subset.eq"),
    (r"\\subset(?![A-Za-z])", "subset"),
    (r"\\cup(?![A-Za-z])", "union"),
    (r"\\cap(?![A-Za-z])", "sect"),
    (r"\\forall(?![A-Za-z])", "forall"),
    (r"\\exists(?![A-Za-z])", "exists"),
    (r"\\lfloor(?![A-Za-z])", "floor.l"),
    (r"\\rfloor(?![A-Za-z])", "floor.r"),
    (r"\\lceil(?![A-Za-z])", "ceil.l"),
    (r"\\rceil(?![A-Za-z])", "ceil.r"),
    (r"\\left(?![A-Za-z])\s*", ""),
    (r"\\right(?![A-Za-z])\s*", ""),
)


class TypstRenderError(RuntimeError):
    """Raised when statement discovery or Typst compilation fails."""


def escape_typst_text(text: str) -> str:
    """Escape Typst special markup characters in plain prose."""
    return _TYPST_ESCAPE_RE.sub(r"\\\1", text)


def _replace_latex_one_arg(expr: str, macro: str, replacer: Any) -> str:
    needle = f"\\{macro}{{"
    out: list[str] = []
    idx = 0
    n = len(expr)
    while idx < n:
        if expr.startswith(needle, idx):
            pos = idx + len(needle)
            depth = 1
            start = pos
            while pos < n and depth > 0:
                if expr[pos] == "{":
                    depth += 1
                elif expr[pos] == "}":
                    depth -= 1
                pos += 1
            if depth == 0:
                inner = expr[start : pos - 1]
                out.append(replacer(inner))
                idx = pos
                continue
        out.append(expr[idx])
        idx += 1
    return "".join(out)


def _replace_latex_two_args(expr: str, macro: str, replacer: Any) -> str:
    needle = f"\\{macro}{{"
    out: list[str] = []
    idx = 0
    n = len(expr)
    while idx < n:
        if expr.startswith(needle, idx):
            pos = idx + len(needle)
            depth = 1
            first_start = pos
            while pos < n and depth > 0:
                if expr[pos] == "{":
                    depth += 1
                elif expr[pos] == "}":
                    depth -= 1
                pos += 1
            if depth == 0 and pos < n and expr[pos] == "{":
                first = expr[first_start : pos - 1]
                pos += 1
                second_start = pos
                depth = 1
                while pos < n and depth > 0:
                    if expr[pos] == "{":
                        depth += 1
                    elif expr[pos] == "}":
                        depth -= 1
                    pos += 1
                if depth == 0:
                    second = expr[second_start : pos - 1]
                    out.append(replacer(first, second))
                    idx = pos
                    continue
        out.append(expr[idx])
        idx += 1
    return "".join(out)


def _replace_latex_frac(expr: str) -> str:
    return _replace_latex_two_args(expr, "frac", lambda num, den: f"frac({num}, {den})")


def _convert_latex_math_to_typst(expr: str) -> str:
    """Translate common competitive-programming LaTeX math tokens to Typst math syntax."""
    res = expr.strip()
    for _ in range(4):
        updated = _replace_latex_frac(res)
        updated = _replace_latex_one_arg(updated, "sqrt", lambda inner: f"sqrt({inner})")
        if updated == res:
            break
        res = updated
    res = _replace_latex_one_arg(
        res, "text", lambda inner: '"' + inner.replace('"', '\\"') + '"'
    )
    res = _replace_latex_one_arg(
        res, "mathrm", lambda inner: '"' + inner.replace('"', '\\"') + '"'
    )
    res = _replace_latex_one_arg(res, "pmod", lambda inner: f"(mod {inner})")
    res = _replace_latex_two_args(res, "binom", lambda a, b: f"binom({a}, {b})")
    for pat, repl in _LATEX_WORD_REPLACEMENTS:
        res = re.sub(pat, repl, res)
    # Convert sub/superscript brace groups a_{i+1} / 10^{18} -> a_(i+1) / 10^(18)
    res = re.sub(r"([_^])\{([^{}]+)\}", r"\1(\2)", res)
    return res.strip()


def _format_inline_code(code: str) -> str:
    if "`" not in code:
        return f"`{code}`"
    escaped = code.replace("\\", "\\\\").replace('"', '\\"')
    return f'#raw("{escaped}")'


def _find_closing_math(line: str, open_pos: int) -> int:
    """Find closing inline ``$`` following Pandoc rules to avoid matching currency ``$100``."""
    n = len(line)
    if open_pos + 1 >= n or line[open_pos + 1].isspace():
        return -1
    pos = open_pos + 1
    while pos < n:
        ch = line[pos]
        if ch == "\\":
            pos += 2
            continue
        if ch == "`":
            # Inline math cannot swallow an unescaped inline code span.
            return -1
        if ch == "$":
            prev_ok = not line[pos - 1].isspace()
            next_is_digit = pos + 1 < n and line[pos + 1].isascii() and line[pos + 1].isdigit()
            if prev_ok and not next_is_digit:
                return pos
        pos += 1
    return -1


def _convert_inline_markdown(text: str) -> str:
    """Convert inline Markdown (code, math, links, bold, italic) while escaping plain prose."""
    out: list[str] = []
    pos = 0
    n = len(text)
    plain_buf: list[str] = []

    def _flush_plain() -> None:
        if plain_buf:
            out.append(escape_typst_text("".join(plain_buf)))
            plain_buf.clear()

    while pos < n:
        ch = text[pos]

        # Backslash escape in Markdown
        if ch == "\\" and pos + 1 < n and text[pos + 1] in "\\`*_{}[]()#+-.!$|<>~@":
            _flush_plain()
            out.append(escape_typst_text(text[pos + 1]))
            pos += 2
            continue

        # Inline code
        if ch == "`":
            tick_end = pos
            while tick_end < n and text[tick_end] == "`":
                tick_end += 1
            fence = text[pos:tick_end]
            close_idx = text.find(fence, tick_end)
            if close_idx != -1:
                _flush_plain()
                out.append(_format_inline_code(text[tick_end:close_idx]))
                pos = close_idx + len(fence)
                continue

        # Inline display math $$...$$ inside a paragraph
        if text.startswith("$$", pos):
            close_idx = text.find("$$", pos + 2)
            if close_idx != -1:
                _flush_plain()
                converted = _convert_latex_math_to_typst(text[pos + 2 : close_idx])
                out.append(f"$ {converted} $")
                pos = close_idx + 2
                continue

        # Inline math $...$
        if ch == "$":
            close_idx = _find_closing_math(text, pos)
            if close_idx > pos + 1:
                _flush_plain()
                converted = _convert_latex_math_to_typst(text[pos + 1 : close_idx])
                out.append(f"${converted}$")
                pos = close_idx + 1
                continue

        # Markdown link [label](url)
        if ch == "[":
            link_match = re.match(r"^\[([^\]]+)\]\(([^)\s]+)\)", text[pos:])
            if link_match:
                _flush_plain()
                label = _convert_inline_markdown(link_match.group(1))
                url = link_match.group(2).replace("\\", "\\\\").replace('"', '\\"')
                out.append(f'#link("{url}")[{label}]')
                pos += link_match.end()
                continue

        # Bold **...** or __...__
        if text.startswith("**", pos) or text.startswith("__", pos):
            delim = text[pos : pos + 2]
            close_idx = text.find(delim, pos + 2)
            if close_idx > pos + 2:
                _flush_plain()
                inner = _convert_inline_markdown(text[pos + 2 : close_idx])
                out.append(f"*{inner}*")
                pos = close_idx + 2
                continue

        # Italic *...* (require non-space after opening and before closing)
        if ch == "*" and pos + 1 < n and not text[pos + 1].isspace():
            close_idx = text.find("*", pos + 1)
            if close_idx > pos + 1 and not text[close_idx - 1].isspace():
                _flush_plain()
                inner = _convert_inline_markdown(text[pos + 1 : close_idx])
                out.append(f"_{inner}_")
                pos = close_idx + 1
                continue

        plain_buf.append(ch)
        pos += 1

    _flush_plain()
    return "".join(out)


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_table_separator(line: str) -> bool:
    cells = _split_table_row(line)
    if not cells:
        return False
    return all(bool(re.fullmatch(r":?-{3,}:?", cell)) for cell in cells)


def markdown_to_typst(
    markdown_text: str,
    *,
    title: str | None = None,
    include_preamble: bool = True,
) -> str:
    """Convert Markdown statement/tutorial text into clean Typst markup."""
    normalized = markdown_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    out: list[str] = []

    if include_preamble:
        out.extend(
            [
                '#set page(paper: "a4", margin: 2cm)',
                "#set text(size: 10.5pt)",
                "#set heading(numbering: none)",
            ]
        )
        if title and title.strip():
            escaped_title = title.strip().replace("\\", "\\\\").replace('"', '\\"')
            out.append(f'#set document(title: "{escaped_title}")')
        out.append("")

    idx = 0
    n = len(lines)
    while idx < n:
        line = lines[idx]
        stripped = line.strip()

        # Blank line
        if not stripped:
            out.append("")
            idx += 1
            continue

        # Fenced code block
        fence_match = _FENCE_OPEN_RE.match(line)
        if fence_match:
            fence = fence_match.group(2)
            fence_char = fence[0]
            fence_len = len(fence)
            lang = fence_match.group(3).strip().split()[0] if fence_match.group(3).strip() else ""
            code_lines: list[str] = []
            idx += 1
            while idx < n:
                cand = lines[idx].strip()
                if cand.startswith(fence_char * fence_len) and set(cand) == {fence_char}:
                    idx += 1
                    break
                code_lines.append(lines[idx])
                idx += 1
            out_fence = "`" * max(3, fence_len + (1 if any("```" in cl for cl in code_lines) else 0))
            out.append(f"{out_fence}{lang}")
            out.extend(code_lines)
            out.append(out_fence)
            continue

        # Multi-line or single-line display math $$...$$
        if stripped.startswith("$$"):
            if stripped.endswith("$$") and len(stripped) >= 4:
                expr = _convert_latex_math_to_typst(stripped[2:-2])
                out.append(f"$ {expr} $")
                idx += 1
                continue
            math_lines = [stripped[2:]]
            idx += 1
            while idx < n:
                m_line = lines[idx]
                if m_line.strip().endswith("$$"):
                    math_lines.append(m_line.strip()[:-2])
                    idx += 1
                    break
                math_lines.append(m_line)
                idx += 1
            expr = _convert_latex_math_to_typst("\n".join(math_lines))
            out.append(f"$ {expr} $")
            continue

        # Heading
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = _convert_inline_markdown(heading_match.group(2).strip())
            out.append(f"{'=' * level} {heading_text}")
            idx += 1
            continue

        # Horizontal rule
        if _HR_RE.match(stripped):
            out.append("#line(length: 100%, stroke: 0.5pt + luma(180))")
            idx += 1
            continue

        # Markdown pipe table
        if "|" in line and idx + 1 < n and "|" in lines[idx + 1] and _is_table_separator(lines[idx + 1]):
            header_cells = _split_table_row(line)
            ncols = len(header_cells)
            idx += 2
            body_rows: list[list[str]] = []
            while idx < n and "|" in lines[idx] and lines[idx].strip():
                row = _split_table_row(lines[idx])
                if len(row) < ncols:
                    row.extend([""] * (ncols - len(row)))
                body_rows.append(row[:ncols])
                idx += 1
            out.append("#table(")
            out.append(f"  columns: {ncols},")
            hdr_rendered = ", ".join(f"[*{_convert_inline_markdown(c)}*]" for c in header_cells)
            out.append(f"  table.header({hdr_rendered}),")
            for row in body_rows:
                row_rendered = ", ".join(f"[{_convert_inline_markdown(c)}]" for c in row)
                out.append(f"  {row_rendered},")
            out.append(")")
            continue

        # Blockquote
        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while idx < n and lines[idx].strip().startswith(">"):
                q_text = lines[idx].strip()[1:].lstrip()
                if q_text:
                    quote_lines.append(_convert_inline_markdown(q_text))
                idx += 1
            joined_q = " ".join(quote_lines)
            out.append(f"#quote[{joined_q}]")
            continue

        # Unordered list
        ul_match = _UL_RE.match(line)
        if ul_match:
            indent = "  " * (len(ul_match.group(1).replace("\t", "  ")) // 2)
            out.append(f"{indent}- {_convert_inline_markdown(ul_match.group(2))}")
            idx += 1
            continue

        # Ordered list
        ol_match = _OL_RE.match(line)
        if ol_match:
            indent = "  " * (len(ol_match.group(1).replace("\t", "  ")) // 2)
            out.append(f"{indent}+ {_convert_inline_markdown(ol_match.group(2))}")
            idx += 1
            continue

        # Standard paragraph line
        out.append(_convert_inline_markdown(line))
        idx += 1

    return "\n".join(out).rstrip() + "\n"


def render_statement_pdf(
    problem_dir: str,
    *,
    output_path: str | None = None,
    include_tutorial: bool = False,
    emit_typ: bool = False,
    typst_bin: str | None = None,
) -> dict[str, Any]:
    """Convert statement (and optional tutorial) to Typst and compile PDF via `typst` if installed."""
    if not os.path.isdir(problem_dir):
        raise TypstRenderError(f"Problem directory not found: {problem_dir}")

    try:
        manifest = load_manifest(problem_dir)
    except (ValidationError, OSError, ValueError):
        manifest = None

    stmt_candidates = (
        [manifest.statement_path, "statements/README.md", "README.md"]
        if manifest is not None
        else ["statements/README.md", "README.md"]
    )
    stmt_abs: str | None = None
    for cand in stmt_candidates:
        resolved = safe_resolve_under_root(problem_dir, cand)
        if resolved is not None and os.path.isfile(resolved):
            stmt_abs = resolved
            break
    if stmt_abs is None:
        raise TypstRenderError("Statement file not found (expected statements/README.md)")

    stmt_md = Path(stmt_abs).read_text(encoding="utf-8", errors="replace")
    title = manifest.problem_name if manifest is not None else os.path.basename(os.path.normpath(problem_dir))
    typ_source = markdown_to_typst(stmt_md, title=title, include_preamble=True)

    tutorial_included = False
    if include_tutorial:
        tut_candidates = (
            [manifest.tutorial_path, "statements/tutorial.md", "tutorial.md"]
            if manifest is not None
            else ["statements/tutorial.md", "tutorial.md"]
        )
        tut_abs: str | None = None
        for cand in tut_candidates:
            resolved = safe_resolve_under_root(problem_dir, cand)
            if resolved is not None and os.path.isfile(resolved):
                tut_abs = resolved
                break
        if tut_abs is None:
            raise TypstRenderError("Tutorial file not found (expected statements/tutorial.md)")
        tut_md = Path(tut_abs).read_text(encoding="utf-8", errors="replace")
        typ_source = (
            typ_source.rstrip()
            + "\n\n#pagebreak()\n\n"
            + markdown_to_typst(tut_md, include_preamble=False)
        )
        tutorial_included = True

    pdf_out = output_path or os.path.join(problem_dir, "statements", "statement.pdf")
    typ_out = str(Path(pdf_out).with_suffix(".typ"))

    if emit_typ:
        Path(typ_out).parent.mkdir(parents=True, exist_ok=True)
        Path(typ_out).write_text(typ_source, encoding="utf-8")

    resolved_typst = typst_bin if typst_bin is not None else shutil.which("typst")
    if not resolved_typst:
        return {
            "success": emit_typ,
            "typst_installed": False,
            "pdf_path": None,
            "typ_path": typ_out if emit_typ else None,
            "tutorial_included": tutorial_included,
            "typ_source": typ_source,
            "instructions": TYPST_INSTALL_INSTRUCTIONS,
        }

    Path(pdf_out).parent.mkdir(parents=True, exist_ok=True)
    tmp_typ: str | None = None
    try:
        if emit_typ:
            compile_input = typ_out
        else:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".typ",
                delete=False,
            ) as tmp_f:
                tmp_f.write(typ_source)
                tmp_typ = tmp_f.name
            compile_input = tmp_typ

        proc = subprocess.run(
            [resolved_typst, "compile", compile_input, pdf_out],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode != 0:
            err_msg = (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()
            raise TypstRenderError(f"typst compile failed: {err_msg}")
    finally:
        if tmp_typ is not None and os.path.lexists(tmp_typ):
            try:
                os.unlink(tmp_typ)
            except OSError:
                pass

    return {
        "success": True,
        "typst_installed": True,
        "pdf_path": os.path.abspath(pdf_out),
        "typ_path": os.path.abspath(typ_out) if emit_typ else None,
        "tutorial_included": tutorial_included,
        "typ_source": typ_source,
        "instructions": None,
    }


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for ``python -m autocode_mcp.cli.render_pdf``."""
    _configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Render AutoCode Markdown statements (and optional tutorial) to PDF via Typst."
    )
    parser.add_argument("problem_dir", help="AutoCode problem directory")
    parser.add_argument(
        "-o",
        "--output",
        dest="output_path",
        default=None,
        help="Output PDF path (defaults to <problem_dir>/statements/statement.pdf)",
    )
    parser.add_argument(
        "--include-tutorial",
        action="store_true",
        help="Append statements/tutorial.md after a page break",
    )
    parser.add_argument(
        "--emit-typ",
        action="store_true",
        help="Write the generated .typ source alongside the output path (works even without typst)",
    )
    args = parser.parse_args(argv)

    try:
        res = render_statement_pdf(
            args.problem_dir,
            output_path=args.output_path,
            include_tutorial=args.include_tutorial,
            emit_typ=args.emit_typ,
        )
    except (TypstRenderError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not res["typst_installed"]:
        if args.emit_typ and res["typ_path"]:
            print(f"Typst source written to: {res['typ_path']}")
            print(
                "Warning: 'typst' binary not found in PATH; PDF compilation skipped.\n"
                + TYPST_INSTALL_INSTRUCTIONS,
                file=sys.stderr,
            )
            return 0
        print(
            "Error: 'typst' binary not found in PATH.\n" + TYPST_INSTALL_INSTRUCTIONS,
            file=sys.stderr,
        )
        return 1

    print(f"Rendered PDF: {res['pdf_path']}")
    if res.get("typ_path"):
        print(f"Typst source: {res['typ_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
