# Orchid 4D, Anti-Semantic-Collapse & Originality Guide

Reference examples for `idea-feasibility`.

## Orchid 4D Probe Examples (`arXiv:2604.21505`)

1. **Lexical Ambiguity**: "path" -> simple path vs walk; "subsequence" -> contiguous subarray vs non-contiguous subsequence; "increasing" -> strictly increasing ($<$) vs non-decreasing ($\le$).
2. **Syntactic Ambiguity**: "count pairs $(i, j)$ with $a_i + a_j = k$" -> unordered ($i < j$) vs ordered ($i \neq j$ or allowing $i = j$).
3. **Semantic Ambiguity**: empty array/graph behavior, disconnected components, self-loops or parallel edges, tie-breaking rules, modulo before vs after comparison, adaptive vs oblivious interactor.
4. **Vague Boundaries**: missing $\sum n$ across testcases, inclusive vs strict inequalities, negative vs zero edge values.

## Anti-Semantic-Collapse Example (`arXiv:2607.01953`)

If a draft says *"Find the shortest path from 1 to n in a weighted graph"*:

- **Reading A**: edges are undirected, weights $\ge 1$, graph may have parallel edges.
- **Reading B**: edges are directed, weights may be $0$, graph may be disconnected (output `-1`).
- If samples pass under both A and B yet diverge on a legal boundary graph, return `decision: no_go` and request clarification.

## Local Originality & Single-Source Header Usage

- Index local archive: `OriginalityIndex.from_directory(corpus_dir)` (combines BM25 + word/char n-grams + optional `Embedder` such as `CPRetriever-Prob`).
- Audit constraints & generate header: `audit_manifest_constraints(manifest)` and `generate_constraints_header(manifest)` from `autocode_mcp.utils.smt_constraints`.
