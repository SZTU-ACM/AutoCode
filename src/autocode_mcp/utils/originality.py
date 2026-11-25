"""Zero-dependency local problem-corpus originality detector with optional dense Embedder protocol."""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from .communication_runner import safe_resolve_under_root

_MAX_CORPUS_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB safety cap per document
_DEFAULT_BM25_K1 = 1.5
_DEFAULT_BM25_B = 0.75

_ASCII_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")

_STOPWORDS = frozenset(
    {
        # Common English prose / CP boilerplate
        "a",
        "an",
        "and",
        "answer",
        "are",
        "as",
        "at",
        "be",
        "between",
        "both",
        "but",
        "by",
        "can",
        "case",
        "cases",
        "compute",
        "contains",
        "determine",
        "each",
        "explanation",
        "find",
        "first",
        "following",
        "for",
        "format",
        "from",
        "given",
        "has",
        "have",
        "if",
        "in",
        "input",
        "integer",
        "integers",
        "into",
        "is",
        "it",
        "its",
        "line",
        "lines",
        "may",
        "must",
        "no",
        "not",
        "number",
        "of",
        "on",
        "one",
        "only",
        "or",
        "output",
        "print",
        "problem",
        "sample",
        "samples",
        "second",
        "should",
        "single",
        "some",
        "statement",
        "such",
        "test",
        "tests",
        "than",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "two",
        "we",
        "where",
        "which",
        "will",
        "with",
        "you",
        "your",
        # LaTeX formatting tokens
        "le",
        "leq",
        "ge",
        "geq",
        "ne",
        "neq",
        "times",
        "cdot",
        "text",
        "dots",
        "ldots",
        "cdots",
        # Common Chinese CP boilerplate
        "给定",
        "一个",
        "一行",
        "输入",
        "输出",
        "格式",
        "样例",
        "解释",
        "说明",
        "数据",
        "范围",
        "满足",
        "其中",
        "表示",
        "对于",
        "所有",
        "测试",
        "整数",
        "正整",
        "请问",
        "需要",
        "可以",
        "如果",
        "那么",
        "每个",
        "第一",
        "第二",
        "包含",
    }
)


@runtime_checkable
class Embedder(Protocol):
    """Optional dense embedding provider protocol (e.g., ``CPRetriever-Prob``)."""

    def embed_texts(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Encode a batch of problem texts into dense embedding vectors."""
        ...


@dataclass(frozen=True)
class ProblemDocument:
    problem_id: str
    title: str = ""
    statement: str = ""
    tags: tuple[str, ...] = ()
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        parts = [self.title.strip(), self.statement.strip()]
        if self.tags:
            parts.append(" ".join(self.tags))
        return "\n".join(part for part in parts if part)


@dataclass(frozen=True)
class OriginalityMatch:
    problem_id: str
    title: str
    source: str
    score: float
    bm25_score: float
    bm25_normalized: float
    word_ngram_cosine: float
    char_ngram_cosine: float
    dense_cosine: float | None
    overlapping_terms: tuple[str, ...]
    risk_level: Literal["high", "medium", "low"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem_id": self.problem_id,
            "title": self.title,
            "source": self.source,
            "score": self.score,
            "bm25_score": self.bm25_score,
            "bm25_normalized": self.bm25_normalized,
            "word_ngram_cosine": self.word_ngram_cosine,
            "char_ngram_cosine": self.char_ngram_cosine,
            "dense_cosine": self.dense_cosine,
            "overlapping_terms": list(self.overlapping_terms),
            "risk_level": self.risk_level,
        }


@dataclass(frozen=True)
class OriginalityReport:
    original: bool
    max_similarity: float
    risk_level: Literal["high", "medium", "low"]
    threshold: float
    corpus_size: int
    used_dense_embedder: bool
    matches: tuple[OriginalityMatch, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "original": self.original,
            "max_similarity": self.max_similarity,
            "risk_level": self.risk_level,
            "threshold": self.threshold,
            "corpus_size": self.corpus_size,
            "used_dense_embedder": self.used_dense_embedder,
            "matches": [m.to_dict() for m in self.matches],
        }


# ---------------------------------------------------------------------------
# Normalization, stemming, tokenization & similarity primitives
# ---------------------------------------------------------------------------


def _stem_ascii(token: str) -> str:
    """Apply lightweight Porter-style suffix normalization for English CP terms."""
    if len(token) <= 3:
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("ied") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("ing") and len(token) > 5:
        stem = token[:-3]
        return stem[:-1] if len(stem) >= 3 and stem[-1] == stem[-2] else stem
    if token.endswith("ed") and len(token) > 4:
        stem = token[:-2]
        return stem[:-1] if len(stem) >= 3 and stem[-1] == stem[-2] else stem
    if token.endswith(("ches", "shes", "sses", "xes", "zes")) and len(token) > 5:
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")) and len(token) > 3:
        return token[:-1]
    return token


def _strip_markdown_noise(text: str) -> str:
    # Strip fenced sample blocks and inline code/math wrappers while keeping prose tokens
    cleaned = re.sub(r"(?s)```.*?```", " ", text)
    cleaned = re.sub(r"(?s)~~~.*?~~~", " ", cleaned)
    cleaned = re.sub(r"[`*$#|>]+", " ", cleaned)
    cleaned = re.sub(r"\\([A-Za-z]+)", r" \1 ", cleaned)
    return cleaned


def _tokenize_terms(text: str) -> list[str]:
    """Extract normalized word stems and CJK bigrams, filtering boilerplate."""
    cleaned = _strip_markdown_noise(text).lower()
    terms: list[str] = []

    for match in _ASCII_TOKEN_RE.finditer(cleaned):
        tok = match.group(0).strip("_")
        if not tok or tok.isdigit():
            continue
        if len(tok) == 1 and tok not in {"k"}:
            continue
        if tok in _STOPWORDS:
            continue
        stemmed = _stem_ascii(tok)
        if stemmed in _STOPWORDS or len(stemmed) <= 1:
            continue
        terms.append(stemmed)

    for cjk_match in _CJK_RUN_RE.finditer(cleaned):
        run = cjk_match.group(0)
        if len(run) == 1:
            terms.append(run)
            continue
        for i in range(len(run) - 1):
            bg = run[i : i + 2]
            if bg not in _STOPWORDS:
                terms.append(bg)
    return terms


def _word_ngrams(terms: Sequence[str]) -> list[str]:
    grams = list(terms)
    for i in range(len(terms) - 1):
        grams.append(f"{terms[i]}_{terms[i + 1]}")
    return grams


def _char_ngrams(text: str, sizes: tuple[int, ...] = (3, 4)) -> list[str]:
    cleaned = _strip_markdown_noise(text).lower()
    # Collapse numbers and punctuation so parameter renames/scale changes do not skew n-grams
    cleaned = re.sub(r"\d+", " ", cleaned)
    cleaned = re.sub(r"[^a-z\u4e00-\u9fff]+", " ", cleaned)
    compact = " ".join(cleaned.split())
    if not compact:
        return []
    grams: list[str] = []
    for n in sizes:
        if len(compact) < n:
            continue
        for i in range(len(compact) - n + 1):
            gram = compact[i : i + n]
            if gram.strip():
                grams.append(gram)
    return grams


def _cosine_dict(vec_a: dict[str, float], norm_a: float, vec_b: dict[str, float], norm_b: float) -> float:
    if norm_a <= 0.0 or norm_b <= 0.0 or not vec_a or not vec_b:
        return 0.0
    if len(vec_a) > len(vec_b):
        vec_a, vec_b = vec_b, vec_a
    dot = sum(val * vec_b[k] for k, val in vec_a.items() if k in vec_b)
    score = dot / (norm_a * norm_b)
    return max(0.0, min(1.0, score))


def _dense_cosine(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for a, b in zip(vec_a, vec_b, strict=True):
        fa = float(a)
        fb = float(b)
        dot += fa * fb
        na += fa * fa
        nb += fb * fb
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    cos = dot / (math.sqrt(na) * math.sqrt(nb))
    return max(0.0, min(1.0, cos))


def _classify_risk(score: float) -> Literal["high", "medium", "low"]:
    if score >= 0.65:
        return "high"
    if score >= 0.35:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Corpus loading with strict path containment
# ---------------------------------------------------------------------------


def _parse_markdown_document(path: Path, rel_path: str) -> ProblemDocument:
    content = path.read_text(encoding="utf-8", errors="replace")
    title = ""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            break
    if path.name.lower() == "readme.md" and path.parent.name.lower() in {"statements", "statement"}:
        pid = path.parent.parent.name if path.parent.parent != path.parent else rel_path
    else:
        pid = rel_path.removesuffix(path.suffix)
    return ProblemDocument(
        problem_id=pid,
        title=title or pid,
        statement=content,
        source=rel_path,
    )


def _parse_jsonl_documents(path: Path, source_label: str) -> list[ProblemDocument]:
    docs: list[ProblemDocument] = []
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    for line_no, raw_line in enumerate(raw_text.splitlines(), 1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        pid = str(
            obj.get("id")
            or obj.get("problem_id")
            or obj.get("slug")
            or obj.get("name")
            or f"{source_label}:{line_no}"
        )
        title = str(obj.get("title") or obj.get("name") or obj.get("problem_name") or "")
        body_parts: list[str] = []
        for field_name in (
            "statement",
            "description",
            "text",
            "content",
            "prompt",
            "body",
            "input_specification",
            "output_specification",
        ):
            val = obj.get(field_name)
            if isinstance(val, str) and val.strip():
                body_parts.append(val.strip())
        statement = "\n".join(body_parts)
        raw_tags = obj.get("tags") or obj.get("algorithm_tags") or ()
        if isinstance(raw_tags, str):
            tags = tuple(t.strip() for t in raw_tags.split(",") if t.strip())
        elif isinstance(raw_tags, list):
            tags = tuple(str(t).strip() for t in raw_tags if str(t).strip())
        else:
            tags = ()
        src = str(obj.get("source") or obj.get("contest") or source_label)
        docs.append(
            ProblemDocument(
                problem_id=pid,
                title=title,
                statement=statement,
                tags=tags,
                source=src,
                metadata={
                    k: v
                    for k, v in obj.items()
                    if k
                    not in {
                        "id",
                        "problem_id",
                        "slug",
                        "title",
                        "statement",
                        "description",
                        "text",
                        "content",
                        "prompt",
                        "body",
                    }
                },
            )
        )
    return docs


# ---------------------------------------------------------------------------
# Indexed document record & OriginalityIndex
# ---------------------------------------------------------------------------


@dataclass
class _IndexedDoc:
    doc: ProblemDocument
    term_tf: Counter[str]
    doc_len: int
    word_tf: Counter[str]
    char_tf: Counter[str]
    word_vec: dict[str, float] = field(default_factory=dict)
    word_norm: float = 0.0
    char_vec: dict[str, float] = field(default_factory=dict)
    char_norm: float = 0.0
    dense_vec: tuple[float, ...] | None = None


class OriginalityIndex:
    """Local BM25 + word/char n-gram cosine similarity problem-corpus originality detector."""

    def __init__(
        self,
        documents: Iterable[ProblemDocument] = (),
        *,
        embedder: Embedder | None = None,
        k1: float = _DEFAULT_BM25_K1,
        b: float = _DEFAULT_BM25_B,
        dense_weight: float = 0.5,
    ) -> None:
        self._embedder = embedder
        self._k1 = k1
        self._b = b
        self._dense_weight = max(0.0, min(1.0, dense_weight))
        self._records: list[_IndexedDoc] = []
        self._term_df: Counter[str] = Counter()
        self._word_df: Counter[str] = Counter()
        self._char_df: Counter[str] = Counter()
        self._avgdl: float = 0.0
        self._dirty = True
        self.skipped_unsafe_paths: list[str] = []
        self.add_documents(documents)

    @property
    def size(self) -> int:
        return len(self._records)

    @property
    def documents(self) -> list[ProblemDocument]:
        return [rec.doc for rec in self._records]

    def add_document(self, doc: ProblemDocument) -> None:
        text = doc.full_text
        terms = _tokenize_terms(text)
        word_grams = _word_ngrams(terms)
        char_grams = _char_ngrams(text)

        term_tf = Counter(terms)
        word_tf = Counter(word_grams)
        char_tf = Counter(char_grams)

        self._term_df.update(term_tf.keys())
        self._word_df.update(word_tf.keys())
        self._char_df.update(char_tf.keys())
        self._records.append(
            _IndexedDoc(
                doc=doc,
                term_tf=term_tf,
                doc_len=len(terms),
                word_tf=word_tf,
                char_tf=char_tf,
            )
        )
        self._dirty = True

    def add_documents(self, docs: Iterable[ProblemDocument]) -> None:
        for doc in docs:
            self.add_document(doc)

    @classmethod
    def from_jsonl(
        cls,
        jsonl_path: str | Path,
        *,
        embedder: Embedder | None = None,
    ) -> OriginalityIndex:
        path = Path(jsonl_path)
        if not path.is_file():
            raise FileNotFoundError(f"JSONL corpus file not found: {jsonl_path}")
        if path.stat().st_size > _MAX_CORPUS_FILE_BYTES * 32:
            raise ValueError(f"JSONL corpus file exceeds safety limit: {jsonl_path}")
        docs = _parse_jsonl_documents(path, path.name)
        return cls(docs, embedder=embedder)

    def add_corpus_file(
        self,
        corpus_root: str | Path,
        rel_path: str,
        *,
        extensions: tuple[str, ...] = (".md", ".jsonl", ".txt"),
    ) -> int:
        """Safely load a single JSONL/Markdown file under ``corpus_root``, rejecting escapes."""
        real_root = os.path.realpath(str(corpus_root))
        resolved_str = safe_resolve_under_root(real_root, rel_path)
        if resolved_str is None:
            raise ValueError(f"Corpus path escapes root or is invalid: {rel_path!r}")
        safe_path = Path(resolved_str)
        if not safe_path.is_file():
            raise FileNotFoundError(f"Corpus file not found: {rel_path}")
        ext = safe_path.suffix.lower()
        allowed_exts = {e.lower() for e in extensions}
        if ext not in allowed_exts:
            raise ValueError(f"Unsupported corpus file extension {ext!r} for {rel_path!r}")
        if safe_path.stat().st_size > _MAX_CORPUS_FILE_BYTES * (32 if ext == ".jsonl" else 1):
            raise ValueError(f"Corpus file exceeds safety size limit: {rel_path!r}")
        norm_rel = os.path.relpath(resolved_str, real_root).replace("\\", "/")
        if ext == ".jsonl":
            loaded = _parse_jsonl_documents(safe_path, norm_rel)
            self.add_documents(loaded)
            return len(loaded)
        self.add_document(_parse_markdown_document(safe_path, norm_rel))
        return 1

    @classmethod
    def from_directory(
        cls,
        corpus_dir: str | Path,
        *,
        embedder: Embedder | None = None,
        extensions: tuple[str, ...] = (".md", ".jsonl", ".txt"),
        strict_containment: bool = False,
    ) -> OriginalityIndex:
        """Load JSONL/Markdown problems from ``corpus_dir`` with strict path containment."""
        root = Path(corpus_dir)
        if not root.is_dir():
            raise FileNotFoundError(f"Corpus directory not found: {corpus_dir}")
        real_root = os.path.realpath(str(root))
        allowed_exts = {ext.lower() for ext in extensions}
        docs: list[ProblemDocument] = []
        skipped_unsafe: list[str] = []

        for current_root, dirnames, filenames in os.walk(real_root, followlinks=False):
            # Prune hidden/cache directories and symlinked directories escaping root
            kept_dirs: list[str] = []
            for dname in sorted(dirnames):
                if dname.startswith(".") or dname in {"__pycache__", "node_modules"}:
                    continue
                candidate_dir = os.path.join(current_root, dname)
                rel_dir = os.path.relpath(candidate_dir, real_root).replace("\\", "/")
                if os.path.islink(candidate_dir):
                    if safe_resolve_under_root(real_root, rel_dir) is None:
                        skipped_unsafe.append(rel_dir)
                        if strict_containment:
                            raise ValueError(
                                f"Unsafe escaping symlink directory inside corpus: {rel_dir!r}"
                            )
                    continue
                if safe_resolve_under_root(real_root, rel_dir) is not None:
                    kept_dirs.append(dname)
            dirnames[:] = kept_dirs

            dir_has_readme = any(fn.lower() == "readme.md" for fn in filenames)
            for fname in sorted(filenames):
                ext = Path(fname).suffix.lower()
                if ext not in allowed_exts:
                    continue
                if fname.lower() in {"tutorial.md", "editorial.md", "changelog.md"} and dir_has_readme:
                    continue
                full_path = os.path.join(current_root, fname)
                rel_file = os.path.relpath(full_path, real_root).replace("\\", "/")
                safe_path_str = safe_resolve_under_root(real_root, rel_file)
                if safe_path_str is None:
                    skipped_unsafe.append(rel_file)
                    if strict_containment:
                        raise ValueError(f"Unsafe escaping path inside corpus directory: {rel_file!r}")
                    continue
                safe_path = Path(safe_path_str)
                try:
                    size_bytes = safe_path.stat().st_size
                except OSError:
                    continue
                if size_bytes > _MAX_CORPUS_FILE_BYTES * (32 if ext == ".jsonl" else 1):
                    continue
                if ext == ".jsonl":
                    docs.extend(_parse_jsonl_documents(safe_path, rel_file))
                else:
                    docs.append(_parse_markdown_document(safe_path, rel_file))

        index = cls(docs, embedder=embedder)
        index.skipped_unsafe_paths.extend(skipped_unsafe)
        return index

    def _idf_bm25(self, df: int, total_docs: int) -> float:
        return math.log(1.0 + (total_docs - df + 0.5) / (df + 0.5))

    def _idf_tfidf(self, df: int, total_docs: int) -> float:
        return math.log((1.0 + total_docs) / (1.0 + df)) + 1.0

    def _build_tfidf_vector(
        self, tf: Counter[str], df_map: Counter[str], total_docs: int
    ) -> tuple[dict[str, float], float]:
        vec: dict[str, float] = {}
        sq_sum = 0.0
        for gram, cnt in tf.items():
            weight = (1.0 + math.log(cnt)) * self._idf_tfidf(df_map.get(gram, 0), total_docs)
            vec[gram] = weight
            sq_sum += weight * weight
        return vec, math.sqrt(sq_sum)

    def _ensure_indexed(self) -> None:
        if not self._dirty:
            return
        n = len(self._records)
        if n == 0:
            self._avgdl = 0.0
            self._dirty = False
            return
        total_len = sum(rec.doc_len for rec in self._records)
        self._avgdl = total_len / n if n > 0 else 0.0
        for rec in self._records:
            rec.word_vec, rec.word_norm = self._build_tfidf_vector(rec.word_tf, self._word_df, n)
            rec.char_vec, rec.char_norm = self._build_tfidf_vector(rec.char_tf, self._char_df, n)

        if self._embedder is not None:
            missing_indices = [i for i, rec in enumerate(self._records) if rec.dense_vec is None]
            if missing_indices:
                texts = [self._records[i].doc.full_text for i in missing_indices]
                embeddings = self._embedder.embed_texts(texts)
                if len(embeddings) != len(missing_indices):
                    raise ValueError(
                        f"Embedder returned {len(embeddings)} vectors for {len(missing_indices)} documents"
                    )
                for idx, emb in zip(missing_indices, embeddings, strict=True):
                    self._records[idx].dense_vec = tuple(float(x) for x in emb)
        self._dirty = False

    def query(
        self,
        statement: str,
        *,
        title: str = "",
        tags: Sequence[str] = (),
        top_k: int = 5,
    ) -> list[OriginalityMatch]:
        """Return top-k most similar documents ranked by combined originality score."""
        if top_k <= 0 or not self._records:
            return []
        self._ensure_indexed()
        n = len(self._records)

        query_text = "\n".join(
            part for part in (title.strip(), statement.strip(), " ".join(tags).strip()) if part
        )
        q_terms = _tokenize_terms(query_text)
        q_term_tf = Counter(q_terms)
        q_word_tf = Counter(_word_ngrams(q_terms))
        q_char_tf = Counter(_char_ngrams(query_text))

        q_word_vec, q_word_norm = self._build_tfidf_vector(q_word_tf, self._word_df, n)
        q_char_vec, q_char_norm = self._build_tfidf_vector(q_char_tf, self._char_df, n)

        q_dense: tuple[float, ...] | None = None
        if self._embedder is not None:
            embedded = self._embedder.embed_texts([query_text])
            if embedded:
                q_dense = tuple(float(x) for x in embedded[0])

        # Compute query-self BM25 upper bound for [0, 1] normalization
        q_len = max(1, len(q_terms))
        avgdl = max(1.0, self._avgdl)
        self_bm25 = 0.0
        for term, q_cnt in q_term_tf.items():
            idf = self._idf_bm25(self._term_df.get(term, 0), n)
            tf_norm = (q_cnt * (self._k1 + 1.0)) / (
                q_cnt + self._k1 * (1.0 - self._b + self._b * (q_len / avgdl))
            )
            self_bm25 += idf * tf_norm

        matches: list[OriginalityMatch] = []
        for rec in self._records:
            bm25_raw = 0.0
            term_contribs: list[tuple[float, str]] = []
            dl = max(1, rec.doc_len)
            norm_denom_base = self._k1 * (1.0 - self._b + self._b * (dl / avgdl))
            for term, q_cnt in q_term_tf.items():
                d_cnt = rec.term_tf.get(term, 0)
                if d_cnt <= 0:
                    continue
                idf = self._idf_bm25(self._term_df.get(term, 0), n)
                q_weight = min(q_cnt, 3)
                tf_score = (d_cnt * (self._k1 + 1.0)) / (d_cnt + norm_denom_base)
                contrib = idf * tf_score * q_weight
                bm25_raw += contrib
                term_contribs.append((contrib, term))

            bm25_norm = (
                max(0.0, min(1.0, bm25_raw / self_bm25)) if self_bm25 > 0.0 else 0.0
            )
            word_cos = _cosine_dict(q_word_vec, q_word_norm, rec.word_vec, rec.word_norm)
            char_cos = _cosine_dict(q_char_vec, q_char_norm, rec.char_vec, rec.char_norm)

            lexical_score = 0.45 * bm25_norm + 0.35 * word_cos + 0.20 * char_cos
            dense_cos: float | None = None
            if q_dense is not None and rec.dense_vec is not None:
                dense_cos = _dense_cosine(q_dense, rec.dense_vec)
                final_score = (1.0 - self._dense_weight) * lexical_score + self._dense_weight * dense_cos
            else:
                final_score = lexical_score

            term_contribs.sort(key=lambda item: (-item[0], item[1]))
            top_terms = tuple(term for _, term in term_contribs[:10])
            matches.append(
                OriginalityMatch(
                    problem_id=rec.doc.problem_id,
                    title=rec.doc.title,
                    source=rec.doc.source,
                    score=round(final_score, 4),
                    bm25_score=round(bm25_raw, 4),
                    bm25_normalized=round(bm25_norm, 4),
                    word_ngram_cosine=round(word_cos, 4),
                    char_ngram_cosine=round(char_cos, 4),
                    dense_cosine=round(dense_cos, 4) if dense_cos is not None else None,
                    overlapping_terms=top_terms,
                    risk_level=_classify_risk(final_score),
                )
            )

        matches.sort(key=lambda m: (-m.score, -m.bm25_score, m.problem_id))
        return matches[:top_k]

    def check(
        self,
        statement: str,
        *,
        title: str = "",
        tags: Sequence[str] = (),
        top_k: int = 5,
        threshold: float = 0.35,
    ) -> OriginalityReport:
        """Evaluate statement originality against the indexed corpus."""
        matches = self.query(statement, title=title, tags=tags, top_k=top_k)
        max_sim = matches[0].score if matches else 0.0
        risk = _classify_risk(max_sim)
        return OriginalityReport(
            original=max_sim < threshold,
            max_similarity=max_sim,
            risk_level=risk,
            threshold=threshold,
            corpus_size=len(self._records),
            used_dense_embedder=self._embedder is not None,
            matches=tuple(matches),
        )
