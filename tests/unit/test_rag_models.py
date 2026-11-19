import math

import pytest

from disputedesk.llm import FakeEmbedder, FakeReranker, get_embedder, get_reranker
from disputedesk.rag.retriever import RetrievedChunk, reciprocal_rank_fusion
from tests.conftest import make_settings


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_fake_embedder_is_deterministic_and_normalised() -> None:
    embedder = FakeEmbedder()
    (a,) = embedder.embed_documents(["merchant response deadline"])
    assert a == embedder.embed_query("merchant response deadline")
    assert len(a) == embedder.dim
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0)


def test_fake_embedder_rewards_shared_words() -> None:
    embedder = FakeEmbedder()
    query = embedder.embed_query("signature confirmation for delivery")
    near, far = embedder.embed_documents(
        ["Delivery proof needs signature confirmation", "Refunds go back to the original card"]
    )
    assert _cosine(query, near) > _cosine(query, far)


def test_fake_embedder_never_returns_zero_vector() -> None:
    assert any(FakeEmbedder().embed_query("!!!"))


def test_fake_reranker_scores_word_overlap() -> None:
    scores = FakeReranker().score("duplicate charge", ["a duplicate charge", "a refund", ""])
    assert scores == [1.0, 0.0, 0.0]
    assert FakeReranker().score("", ["x"]) == [0.0]


def test_factories_pick_fakes_and_none() -> None:
    settings = make_settings(embedding_provider="fake", reranker_provider="fake")
    assert get_embedder(settings).provider == "fake"
    reranker = get_reranker(settings)
    assert reranker is not None and reranker.provider == "fake"
    assert get_reranker(make_settings(reranker_provider="none")) is None


def test_rag_defaults_are_local_fastembed_models() -> None:
    settings = make_settings()
    assert settings.embedding_provider == "fastembed"
    assert settings.embedding_model == "BAAI/bge-base-en-v1.5"
    assert settings.reranker_model == "BAAI/bge-reranker-base"


def test_rrf_sums_reciprocal_ranks() -> None:
    a, b, c = ("x.md", 0), ("y.md", 0), ("z.md", 0)
    scores = reciprocal_rank_fusion([[a, b], [b, c]], k=60)
    assert scores[a] == pytest.approx(1 / 61)
    assert scores[b] == pytest.approx(1 / 62 + 1 / 61)
    assert scores[c] == pytest.approx(1 / 62)
    assert max(scores, key=lambda key: scores[key]) == b


def test_rrf_handles_empty_lists() -> None:
    assert reciprocal_rank_fusion([[], []]) == {}


def test_citation_format() -> None:
    chunk = RetrievedChunk(
        source="08-time-limits.md",
        section="Merchant deadlines",
        doc_title="Time Limits",
        chunk_index=3,
        content="30 days",
        score=0.1,
        rrf_score=0.1,
    )
    assert chunk.citation == "08-time-limits.md § Merchant deadlines"
