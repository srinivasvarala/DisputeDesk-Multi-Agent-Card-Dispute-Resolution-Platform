"""Ingest + retriever against real Postgres/pgvector, using the deterministic fake models.

Uses its own table so it never touches the real `policy_chunks` index. Retrieval quality with
the real models is measured separately by the retrieval test set (hit@5).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

import psycopg
import pytest

from disputedesk.db import connect
from disputedesk.llm import FakeEmbedder, FakeReranker
from disputedesk.rag.ingest import Chunk, IngestError, chunk_markdown, ingest, load_corpus
from disputedesk.rag.retriever import Retriever, RetrieverError

TABLE = "policy_chunks_test"
POLICIES = Path(__file__).resolve().parents[2] / "data" / "policies"


@pytest.fixture
def rag_conn(database_url: str) -> Iterator[psycopg.Connection]:
    with connect(database_url) as connection:
        connection.execute(f"DROP TABLE IF EXISTS {TABLE}")
        yield connection
        connection.execute(f"DROP TABLE IF EXISTS {TABLE}")


@pytest.fixture
def corpus() -> list[Chunk]:
    return load_corpus(POLICIES)


def _count(conn: psycopg.Connection) -> int:
    row = conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone()
    assert row is not None
    return int(row[0])


# --------------------------------------------------------------------------- ingest


def test_first_ingest_inserts_every_chunk(
    rag_conn: psycopg.Connection, corpus: list[Chunk]
) -> None:
    report = ingest(rag_conn, FakeEmbedder(), corpus, table=TABLE)
    assert report.sources == 15
    assert report.inserted == report.chunks == len(corpus) == _count(rag_conn)
    assert report.updated == report.unchanged == report.deleted == 0


def test_reingest_is_a_no_op(rag_conn: psycopg.Connection, corpus: list[Chunk]) -> None:
    ingest(rag_conn, FakeEmbedder(), corpus, table=TABLE)
    before = rag_conn.execute(f"SELECT source, chunk_index, ingested_at FROM {TABLE}").fetchall()

    class CountingEmbedder(FakeEmbedder):
        calls = 0

        def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            CountingEmbedder.calls += len(texts)
            return super().embed_documents(texts)

    report = ingest(rag_conn, CountingEmbedder(), corpus, table=TABLE)
    assert report.unchanged == len(corpus)
    assert report.inserted == report.updated == report.deleted == 0
    assert CountingEmbedder.calls == 0  # nothing re-embedded
    after = rag_conn.execute(f"SELECT source, chunk_index, ingested_at FROM {TABLE}").fetchall()
    assert sorted(after) == sorted(before)


def test_changed_added_and_removed_chunks_are_synced(rag_conn: psycopg.Connection) -> None:
    v1 = chunk_markdown("# T\n\n## A\n\nalpha\n\n## B\n\nbeta\n\n## C\n\ngamma\n", "t.md")
    ingest(rag_conn, FakeEmbedder(), v1, table=TABLE)

    v2 = chunk_markdown("# T\n\n## A\n\nalpha\n\n## B\n\nbeta changed\n", "t.md")
    v2 += chunk_markdown("# U\n\n## New\n\nnew doc\n", "u.md")
    report = ingest(rag_conn, FakeEmbedder(), v2, table=TABLE)
    assert (report.unchanged, report.updated, report.inserted, report.deleted) == (1, 1, 1, 1)

    rows = rag_conn.execute(
        f"SELECT source, section, content FROM {TABLE} ORDER BY source, chunk_index"
    ).fetchall()
    assert rows == [
        ("t.md", "A", "alpha"),
        ("t.md", "B", "beta changed"),
        ("u.md", "New", "new doc"),
    ]


def test_new_embedding_model_reembeds_everything(
    rag_conn: psycopg.Connection, corpus: list[Chunk]
) -> None:
    ingest(rag_conn, FakeEmbedder(), corpus, table=TABLE)

    class OtherModel(FakeEmbedder):
        model = "fake-hash-256-v2"

    report = ingest(rag_conn, OtherModel(), corpus, table=TABLE)
    assert report.updated == len(corpus)


def test_dimension_change_requires_rebuild(
    rag_conn: psycopg.Connection, corpus: list[Chunk]
) -> None:
    ingest(rag_conn, FakeEmbedder(dim=256), corpus, table=TABLE)
    with pytest.raises(IngestError, match="--rebuild"):
        ingest(rag_conn, FakeEmbedder(dim=128), corpus, table=TABLE)
    assert _count(rag_conn) == len(corpus)  # old index untouched

    report = ingest(rag_conn, FakeEmbedder(dim=128), corpus, table=TABLE, rebuild=True)
    assert report.inserted == len(corpus)


def test_failed_ingest_rolls_back(rag_conn: psycopg.Connection, corpus: list[Chunk]) -> None:
    ingest(rag_conn, FakeEmbedder(), corpus, table=TABLE)

    class Broken(FakeEmbedder):
        model = "broken"

        def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            raise RuntimeError("model crashed")

    with pytest.raises(RuntimeError, match="model crashed"):
        ingest(rag_conn, Broken(), corpus, table=TABLE, rebuild=True)
    # The rebuild's DROP TABLE was rolled back with everything else.
    assert _count(rag_conn) == len(corpus)


def test_tsvector_is_generated(rag_conn: psycopg.Connection) -> None:
    ingest(
        rag_conn,
        FakeEmbedder(),
        chunk_markdown("# T\n\n## Refunds\n\ncards\n", "t.md"),
        table=TABLE,
    )
    row = rag_conn.execute(f"SELECT tsv::text FROM {TABLE}").fetchone()
    assert row is not None
    assert "'refund':" in row[0] and "'card':" in row[0]


# --------------------------------------------------------------------------- retriever


@pytest.fixture
def retriever(rag_conn: psycopg.Connection, corpus: list[Chunk]) -> Retriever:
    ingest(rag_conn, FakeEmbedder(), corpus, table=TABLE)
    return Retriever(rag_conn, FakeEmbedder(), FakeReranker(), table=TABLE)


def test_results_carry_source_and_section(retriever: Retriever) -> None:
    results = retriever.search("signature confirmation for orders above $250", k=5)
    assert len(results) == 5
    assert all(r.source.endswith(".md") and r.section and r.content for r in results)
    assert any(
        r.source == "04-merchandise-not-received.md" and "Signature confirmation" in r.section
        for r in results
    )


@pytest.mark.parametrize("mode", ["hybrid", "vector", "keyword"])
def test_every_mode_returns_ranked_results(retriever: Retriever, mode: str) -> None:
    results = retriever.search("duplicate charge already refunded", k=3, mode=mode, rerank=False)  # type: ignore[arg-type]
    assert 0 < len(results) <= 3
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)
    if mode == "vector":
        assert all(r.vector_rank is not None and r.keyword_rank is None for r in results)
    if mode == "keyword":
        assert all(r.keyword_rank is not None and r.vector_rank is None for r in results)


def test_hybrid_fuses_both_lists(retriever: Retriever) -> None:
    results = retriever.search("merchant response deadline", k=10, rerank=False)
    assert any(r.vector_rank is not None and r.keyword_rank is not None for r in results)
    assert all(r.rerank_score is None and r.score == r.rrf_score for r in results)


def test_rerank_reorders_by_reranker_score(retriever: Retriever) -> None:
    results = retriever.search("counterfeit goods returned", k=5)
    assert all(r.rerank_score is not None for r in results)
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)


def test_keyword_search_with_only_stopwords_returns_nothing(retriever: Retriever) -> None:
    assert retriever.search("the and of", mode="keyword") == []


def test_keyword_search_tolerates_tsquery_syntax(retriever: Retriever) -> None:
    # Characters that mean something in tsquery syntax must be treated as plain text.
    results = retriever.search("refund & (fees) | 'interest' !:*", mode="keyword")
    assert results


@pytest.mark.parametrize("query", ["", "   ", "x" * 1001])
def test_invalid_queries_are_rejected(retriever: Retriever, query: str) -> None:
    with pytest.raises(ValueError):
        retriever.search(query)


def test_model_mismatch_is_detected(rag_conn: psycopg.Connection, retriever: Retriever) -> None:
    class OtherModel(FakeEmbedder):
        model = "other"

    with pytest.raises(RetrieverError, match="re-run"):
        Retriever(rag_conn, OtherModel(), table=TABLE).search("refund")


def test_missing_index_is_reported(rag_conn: psycopg.Connection) -> None:
    with pytest.raises(RetrieverError, match="not found"):
        Retriever(rag_conn, FakeEmbedder(), table=TABLE).search("refund")


class CountingReranker(FakeReranker):
    def __init__(self) -> None:
        self.pool_sizes: list[int] = []

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        self.pool_sizes.append(len(documents))
        return super().score(query, documents)


def test_rerank_pool_is_capped_at_top_n(rag_conn: psycopg.Connection, corpus: list[Chunk]) -> None:
    ingest(rag_conn, FakeEmbedder(), corpus, table=TABLE)
    reranker = CountingReranker()
    retriever = Retriever(rag_conn, FakeEmbedder(), reranker, table=TABLE, rerank_top_n=4)
    unreranked = retriever.search("merchant response deadline", k=10, rerank=False)

    assert len(retriever.search("merchant response deadline", k=3)) == 3
    assert reranker.pool_sizes == [4]  # top 4 fused candidates only

    # The pool is never smaller than k, so every returned chunk carries a rerank score.
    results = retriever.search("merchant response deadline", k=6)
    assert reranker.pool_sizes[-1] == 6
    assert all(r.rerank_score is not None for r in results)
    # The reranked pool is exactly the top-6 fused candidates, reordered.
    assert {(r.source, r.chunk_index) for r in results} == {
        (r.source, r.chunk_index) for r in unreranked[:6]
    }


def test_rerank_top_n_must_be_positive(rag_conn: psycopg.Connection) -> None:
    with pytest.raises(ValueError, match="rerank_top_n"):
        Retriever(rag_conn, FakeEmbedder(), FakeReranker(), table=TABLE, rerank_top_n=0)
