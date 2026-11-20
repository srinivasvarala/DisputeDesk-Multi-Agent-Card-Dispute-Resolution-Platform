"""Hybrid retrieval over the policy chunks: vector + keyword, fused with RRF, then reranked.

1. Vector search: cosine distance on the pgvector column (HNSW index).
2. Keyword search: the query's stemmed terms OR-ed into a tsquery, ranked with ts_rank on the
   weighted tsvector (headings count more than body text). OR rather than AND, because a
   natural-language question rarely has every one of its words in a single chunk.
3. Reciprocal rank fusion: score(d) = sum over lists of 1 / (rrf_k + rank). Rank-based, so the
   two lists' incomparable scores never need normalising.
4. Optional cross-encoder rerank of the top `rerank_top_n` fused candidates. The cross-encoder
   scores each (query, chunk) pair separately, so its cost grows with the pool size; RRF
   already puts the right chunks near the top, so a small pool keeps most of the gain.

Every result carries `source` and `section` so callers can cite it.

    uv run python -m disputedesk.rag.retriever "How long does a merchant have to respond?"
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Literal

import psycopg
from psycopg import sql
from pydantic import BaseModel, Field

from disputedesk.config import get_settings
from disputedesk.db import connect
from disputedesk.llm import Embedder, Reranker, get_embedder, get_reranker
from disputedesk.rag.ingest import DEFAULT_TABLE, check_table_name, to_pgvector

SearchMode = Literal["hybrid", "vector", "keyword"]
ChunkKey = tuple[str, int]

DEFAULT_CANDIDATE_K = 20
DEFAULT_RRF_K = 60  # the constant from the original RRF paper; damps the top ranks
DEFAULT_RERANK_TOP_N = 10
MAX_QUERY_CHARS = 1000


class RetrieverError(RuntimeError):
    pass


class RetrievedChunk(BaseModel):
    source: str = Field(description="Policy document file name")
    section: str = Field(description="Heading path inside the document")
    doc_title: str
    chunk_index: int
    content: str
    score: float = Field(description="Final ranking score: rerank score if reranked, else RRF")
    rrf_score: float
    vector_rank: int | None = Field(default=None, description="1-based rank in vector search")
    keyword_rank: int | None = Field(default=None, description="1-based rank in keyword search")
    rerank_score: float | None = None

    @property
    def citation(self) -> str:
        return f"{self.source} § {self.section}"

    @property
    def rerank_text(self) -> str:
        # Same text the chunk was embedded with (see ingest.Chunk.embedding_text).
        return f"{self.doc_title}\n{self.section}\n\n{self.content}"


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[ChunkKey]], *, k: int = DEFAULT_RRF_K
) -> dict[ChunkKey, float]:
    """Fuse ranked lists of keys. Items missing from a list simply get nothing from it."""
    scores: dict[ChunkKey, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return scores


_COLUMNS = sql.SQL("source, chunk_index, doc_title, section, content")
_Row = tuple[str, int, str, str, str]


class Retriever:
    def __init__(
        self,
        conn: psycopg.Connection,
        embedder: Embedder,
        reranker: Reranker | None = None,
        *,
        table: str = DEFAULT_TABLE,
        candidate_k: int = DEFAULT_CANDIDATE_K,
        rrf_k: int = DEFAULT_RRF_K,
        rerank_top_n: int = DEFAULT_RERANK_TOP_N,
    ) -> None:
        if rerank_top_n < 1:
            raise ValueError("rerank_top_n must be at least 1")
        self._conn = conn
        self._embedder = embedder
        self._reranker = reranker
        self._table = sql.Identifier(check_table_name(table))
        self._candidate_k = candidate_k
        self._rrf_k = rrf_k
        self._rerank_top_n = rerank_top_n
        self._index_checked = False

    def search(
        self, query: str, *, k: int = 5, mode: SearchMode = "hybrid", rerank: bool = True
    ) -> list[RetrievedChunk]:
        """Top-k chunks for the query. `rerank` is ignored when no reranker is configured."""
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        if len(query) > MAX_QUERY_CHARS:
            raise ValueError(f"query longer than {MAX_QUERY_CHARS} characters")
        if k < 1:
            raise ValueError("k must be at least 1")
        self._check_index()

        rows: dict[ChunkKey, _Row] = {}
        vector_keys: list[ChunkKey] = []
        keyword_keys: list[ChunkKey] = []
        if mode in ("hybrid", "vector"):
            vector_keys = self._collect(self._vector_search(query), rows)
        if mode in ("hybrid", "keyword"):
            keyword_keys = self._collect(self._keyword_search(query), rows)

        fused = reciprocal_rank_fusion([vector_keys, keyword_keys], k=self._rrf_k)
        vector_rank = {key: i for i, key in enumerate(vector_keys, start=1)}
        keyword_rank = {key: i for i, key in enumerate(keyword_keys, start=1)}
        # Tie-break on the key so results are deterministic.
        ordered = sorted(fused, key=lambda key: (-fused[key], key))

        results = [
            RetrievedChunk(
                source=rows[key][0],
                chunk_index=rows[key][1],
                doc_title=rows[key][2],
                section=rows[key][3],
                content=rows[key][4],
                score=fused[key],
                rrf_score=fused[key],
                vector_rank=vector_rank.get(key),
                keyword_rank=keyword_rank.get(key),
            )
            for key in ordered
        ]
        if rerank and self._reranker is not None and results:
            # Never rerank fewer than k, so every returned chunk has a rerank score.
            results = results[: max(self._rerank_top_n, k)]
            scores = self._reranker.score(query, [r.rerank_text for r in results])
            if len(scores) != len(results):
                raise RetrieverError(f"reranker returned {len(scores)} scores for {len(results)}")
            results = [
                r.model_copy(update={"score": s, "rerank_score": s})
                for r, s in zip(results, scores, strict=True)
            ]
            results.sort(key=lambda r: (-r.score, r.source, r.chunk_index))
        return results[:k]

    # ----------------------------------------------------------------- internals

    def _check_index(self) -> None:
        """Fail loudly if the index is missing or was built with a different embedding model,
        instead of silently returning meaningless vector matches."""
        if self._index_checked:
            return
        try:
            rows = self._conn.execute(
                sql.SQL("SELECT DISTINCT embedding_model FROM {}").format(self._table)
            ).fetchall()
        except psycopg.errors.UndefinedTable as exc:
            raise RetrieverError("policy index not found; run disputedesk.rag.ingest") from exc
        models = {str(r[0]) for r in rows}
        if not models:
            raise RetrieverError("policy index is empty; run disputedesk.rag.ingest")
        if models != {self._embedder.model}:
            raise RetrieverError(
                f"policy index was built with {sorted(models)} but the retriever embeds with "
                f"{self._embedder.model}; re-run disputedesk.rag.ingest"
            )
        self._index_checked = True

    @staticmethod
    def _collect(found: list[_Row], rows: dict[ChunkKey, _Row]) -> list[ChunkKey]:
        keys = []
        for row in found:
            key = (row[0], row[1])
            rows[key] = row
            keys.append(key)
        return keys

    def _vector_search(self, query: str) -> list[_Row]:
        vector = to_pgvector(self._embedder.embed_query(query))
        stmt = sql.SQL(
            "SELECT {cols} FROM {table} ORDER BY embedding <=> %s::vector LIMIT %s"
        ).format(cols=_COLUMNS, table=self._table)
        return [_as_row(r) for r in self._conn.execute(stmt, (vector, self._candidate_k))]

    def _keyword_search(self, query: str) -> list[_Row]:
        # Stem with the same 'english' config as the index, then OR the lexemes together.
        # quote_literal keeps punctuation in a lexeme from being parsed as tsquery syntax;
        # 'simple' stops to_tsquery from stemming the already-stemmed lexemes again.
        stmt = sql.SQL(
            """
            WITH q AS (
                SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS query
                FROM unnest(tsvector_to_array(to_tsvector('english', %s))) AS lexeme
            )
            SELECT {cols} FROM {table}, q
            WHERE q.query IS NOT NULL AND tsv @@ q.query
            ORDER BY ts_rank(tsv, q.query) DESC, source, chunk_index
            LIMIT %s
            """
        ).format(cols=_COLUMNS, table=self._table)
        return [_as_row(r) for r in self._conn.execute(stmt, (query, self._candidate_k))]


def _as_row(r: tuple[object, ...]) -> _Row:
    return (str(r[0]), int(str(r[1])), str(r[2]), str(r[3]), str(r[4]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search the policy index.")
    parser.add_argument("query")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--mode", choices=["hybrid", "vector", "keyword"], default="hybrid")
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--table", default=DEFAULT_TABLE)
    args = parser.parse_args(argv)

    settings = get_settings()
    reranker = None if args.no_rerank else get_reranker(settings)
    with connect() as conn:
        retriever = Retriever(conn, get_embedder(settings), reranker, table=args.table)
        try:
            results = retriever.search(args.query, k=args.k, mode=args.mode)
        except (RetrieverError, ValueError) as exc:
            print(f"search failed: {exc}", file=sys.stderr)
            return 1
    for i, r in enumerate(results, start=1):
        print(
            f"{i}. [{r.score:.4f}] {r.citation}  (vec={r.vector_rank} kw={r.keyword_rank})\n"
            f"   {r.content[:160].replace(chr(10), ' ')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
