# ADR-004: Hybrid retrieval with a reranker

- Status: accepted
- Date: 2026-11-19

## Context

The policy agent must cite the rulebook. Questions mix exact terms ("$250", "signature", reason codes) with
paraphrase ("they never delivered it"), so neither keyword nor vector search alone is reliable. The corpus is
small (15 documents, 92 chunks), so the choice should be made by measurement, not by habit.

## Decision

One Postgres table with a `vector(768)` column (HNSW) and a generated, weighted `tsvector` (headings A, body B).
A query runs vector top 20 and keyword top 20, merges them with reciprocal rank fusion (k=60), then reranks the
top 10 with a cross-encoder. Embeddings and reranker (`bge-base-en-v1.5`, `bge-reranker-v2-m3-int8`) run locally
through fastembed in `llm.py`: no API key, and CI works offline once the weights are cached.

Keyword queries OR their stemmed terms, because natural-language questions almost never matched with AND.

## Consequences

- On 25 labelled questions only the `bge-reranker-v2-m3-int8` reranker beat no reranking on both hit@5 and MRR.
  Reranking only the top 10 kept the quality of the full pool at about a fifth of the latency.
- **Grounding is enforced in code.** The model can only cite excerpts it was shown, by id, with a quote that
  must appear in the excerpt; the source and section come from the chunk, never from the model.
- One database for cases, audit and vectors keeps the stack small. It would not suit a large corpus.
- Reranking costs about 2.5 s a query on a laptop CPU, and the first run downloads about 0.8 GB.
- Two of 25 questions miss in every configuration (an everyday-wording query and an answer inside a table).
  25 questions is small: a 0.03 MRR difference is one or two rank changes.
- Re-run `python -m disputedesk.rag.ingest` after any edit to `data/policies/`.
