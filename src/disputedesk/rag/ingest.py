"""Ingest the policy markdown corpus into Postgres for hybrid retrieval.

Pipeline: markdown -> heading-aware chunks -> embeddings -> one table holding both a pgvector
column (semantic search) and a generated, weighted tsvector column (keyword search).

Idempotent: chunks are keyed by (source, chunk_index) and carry a content hash. Re-running
embeds only new or changed chunks, deletes chunks that no longer exist, and leaves unchanged
rows alone. The whole sync runs in one transaction.

    uv run python -m disputedesk.rag.ingest            # sync data/policies
    uv run python -m disputedesk.rag.ingest --rebuild  # drop and rebuild (e.g. new model dim)
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections.abc import Iterator
from pathlib import Path

import psycopg
from psycopg import sql
from pydantic import BaseModel, ConfigDict, Field

from disputedesk.config import get_settings
from disputedesk.db import connect
from disputedesk.llm import Embedder, get_embedder

DEFAULT_TABLE = "policy_chunks"
# ~350 tokens of English prose: inside the 512-token window of bge models, with room for the
# title/section header prepended at embedding time.
DEFAULT_MAX_CHARS = 1500
SECTION_SEPARATOR = " > "
INTRO_SECTION = "Introduction"

# A closing run of #s only counts when preceded by whitespace (CommonMark): "C#" keeps its #.
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+)?\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_TABLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")
# The disclaimer banner repeated at the top of every policy doc carries no retrievable content.
_DISCLAIMER_MARKER = "**Fictional policy.**"


class IngestError(RuntimeError):
    pass


class Chunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str = Field(description="File name of the policy document, e.g. 08-time-limits.md")
    doc_title: str = Field(description="The document's H1 title")
    section: str = Field(description="Heading path below the title, joined with ' > '")
    chunk_index: int = Field(ge=0, description="Position of the chunk within its document")
    content: str

    @property
    def embedding_text(self) -> str:
        """What gets embedded and reranked: content with its document and section as context."""
        return f"{self.doc_title}\n{self.section}\n\n{self.content}"

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.embedding_text.encode()).hexdigest()


class IngestReport(BaseModel):
    sources: int
    chunks: int
    inserted: int
    updated: int
    unchanged: int
    deleted: int


# --------------------------------------------------------------------------- chunking


def _is_disclaimer(paragraph: str) -> bool:
    lines = paragraph.splitlines()
    return all(line.lstrip().startswith(">") for line in lines) and _DISCLAIMER_MARKER in lines[0]


def _paragraphs(body: str) -> list[str]:
    """Split on blank lines, but never inside a fenced code block."""
    paragraphs: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        if not line.strip() and not in_fence:
            if current:
                paragraphs.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append("\n".join(current))
    return paragraphs


def _pack(paragraphs: list[str], max_chars: int) -> Iterator[str]:
    """Greedily pack whole paragraphs into pieces of at most max_chars. A single paragraph
    longer than max_chars (e.g. a big table) is kept whole rather than cut mid-row."""
    piece: list[str] = []
    size = 0
    for paragraph in paragraphs:
        if piece and size + len(paragraph) + 2 > max_chars:
            yield "\n\n".join(piece)
            piece, size = [], 0
        piece.append(paragraph)
        size += len(paragraph) + 2
    if piece:
        yield "\n\n".join(piece)


def chunk_markdown(text: str, source: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> list[Chunk]:
    """Split a markdown document into chunks that never cross a heading.

    The first H1 is the document title. Every deeper heading starts a new section whose name is
    the full heading path (e.g. "Delivery proof > Signature confirmation above $250"), so a
    chunk is always citable by document and section. Text before the first sub-heading belongs
    to the "Introduction" section. Long sections are split on paragraph boundaries.
    """
    title: str | None = None
    path: list[tuple[int, str]] = []
    sections: list[tuple[str, str]] = []  # (section name, body)
    body: list[str] = []
    in_fence = False

    def flush() -> None:
        name = SECTION_SEPARATOR.join(heading for _, heading in path) or INTRO_SECTION
        sections.append((name, "\n".join(body)))
        body.clear()

    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match is None:
            body.append(line)
            continue
        flush()
        level, heading = len(match.group(1)), match.group(2).strip()
        if level == 1 and title is None:
            title = heading
            path = []
        else:
            path = [(lvl, h) for lvl, h in path if lvl < level] + [(level, heading)]
    flush()

    doc_title = title or Path(source).stem
    chunks: list[Chunk] = []
    for section, section_body in sections:
        paragraphs = [p for p in _paragraphs(section_body) if not _is_disclaimer(p)]
        for content in _pack(paragraphs, max_chars):
            chunks.append(
                Chunk(
                    source=source,
                    doc_title=doc_title,
                    section=section,
                    chunk_index=len(chunks),
                    content=content.strip(),
                )
            )
    return chunks


def load_corpus(policies_dir: Path, *, max_chars: int = DEFAULT_MAX_CHARS) -> list[Chunk]:
    """Chunk every *.md file in the directory (sorted, so chunk order is stable)."""
    files = sorted(policies_dir.glob("*.md"))
    if not files:
        # Refuse rather than "sync" an empty corpus, which would wipe the index.
        raise IngestError(f"no markdown files found in {policies_dir}")
    chunks: list[Chunk] = []
    for path in files:
        chunks.extend(
            chunk_markdown(path.read_text(encoding="utf-8"), path.name, max_chars=max_chars)
        )
    return chunks


# --------------------------------------------------------------------------- storage


def check_table_name(table: str) -> str:
    if not _TABLE_NAME.match(table):
        raise ValueError(f"invalid table name: {table!r}")
    return table


def to_pgvector(vector: list[float]) -> str:
    """pgvector text literal; avoids a dependency on the pgvector Python package."""
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


def _embedding_type(conn: psycopg.Connection, table: str) -> str | None:
    row = conn.execute(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute"
        " WHERE attrelid = to_regclass(%s) AND attname = 'embedding' AND NOT attisdropped",
        (table,),
    ).fetchone()
    return str(row[0]) if row else None


def ensure_schema(
    conn: psycopg.Connection, dim: int, *, table: str = DEFAULT_TABLE, rebuild: bool = False
) -> None:
    """Create the chunk table and indexes if missing. The vector size is fixed per table, so a
    model with a different dimension needs `rebuild=True` (drop and recreate)."""
    check_table_name(table)
    ident = sql.Identifier(table)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    if rebuild:
        conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(ident))
    existing = _embedding_type(conn, table)
    if existing is not None and existing != f"vector({dim})":
        raise IngestError(
            f"table {table} stores {existing} but the embedder produces vector({dim}); "
            "re-run ingest with --rebuild"
        )
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {table} (
                source          TEXT NOT NULL,
                chunk_index     INTEGER NOT NULL CHECK (chunk_index >= 0),
                doc_title       TEXT NOT NULL,
                section         TEXT NOT NULL,
                content         TEXT NOT NULL,
                content_hash    TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding       vector({dim}) NOT NULL,
                -- Title and section headings weigh more (A) than body text (B).
                tsv tsvector GENERATED ALWAYS AS (
                    setweight(to_tsvector('english', doc_title || ' ' || section), 'A')
                    || setweight(to_tsvector('english', content), 'B')
                ) STORED,
                ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (source, chunk_index)
            );
            CREATE INDEX IF NOT EXISTS {hnsw} ON {table}
                USING hnsw (embedding vector_cosine_ops);
            CREATE INDEX IF NOT EXISTS {gin} ON {table} USING gin (tsv);
            """
        ).format(
            table=ident,
            dim=sql.Literal(dim),
            hnsw=sql.Identifier(f"{table}_embedding_hnsw"),
            gin=sql.Identifier(f"{table}_tsv_gin"),
        )
    )


def ingest(
    conn: psycopg.Connection,
    embedder: Embedder,
    chunks: list[Chunk],
    *,
    table: str = DEFAULT_TABLE,
    rebuild: bool = False,
) -> IngestReport:
    """Make the table match `chunks` exactly, embedding only what changed.

    Everything, including schema changes and `rebuild`, happens in one transaction: if
    embedding or writing fails, the previous index is left intact.
    """
    ident = sql.Identifier(table)
    wanted = {(c.source, c.chunk_index): c for c in chunks}

    with conn.transaction():
        ensure_schema(conn, embedder.dim, table=table, rebuild=rebuild)
        # Serialise concurrent ingests; readers are not blocked.
        conn.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(ident))
        existing: dict[tuple[str, int], tuple[str, str]] = {
            (str(source), int(index)): (str(digest), str(model))
            for source, index, digest, model in conn.execute(
                sql.SQL("SELECT source, chunk_index, content_hash, embedding_model FROM {}").format(
                    ident
                )
            ).fetchall()
        }

        changed = [
            c for key, c in wanted.items() if existing.get(key) != (c.content_hash, embedder.model)
        ]
        vectors = embedder.embed_documents([c.embedding_text for c in changed]) if changed else []
        if len(vectors) != len(changed):
            raise IngestError(f"embedder returned {len(vectors)} vectors for {len(changed)} texts")

        upsert = sql.SQL(
            """
            INSERT INTO {} (source, chunk_index, doc_title, section, content, content_hash,
                            embedding_model, embedding, ingested_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, now())
            ON CONFLICT (source, chunk_index) DO UPDATE SET
                doc_title = EXCLUDED.doc_title,
                section = EXCLUDED.section,
                content = EXCLUDED.content,
                content_hash = EXCLUDED.content_hash,
                embedding_model = EXCLUDED.embedding_model,
                embedding = EXCLUDED.embedding,
                ingested_at = EXCLUDED.ingested_at
            """
        ).format(ident)
        with conn.cursor() as cur:
            cur.executemany(
                upsert,
                [
                    (
                        c.source,
                        c.chunk_index,
                        c.doc_title,
                        c.section,
                        c.content,
                        c.content_hash,
                        embedder.model,
                        to_pgvector(v),
                    )
                    for c, v in zip(changed, vectors, strict=True)
                ],
            )

        stale = [key for key in existing if key not in wanted]
        if stale:
            with conn.cursor() as cur:
                cur.executemany(
                    sql.SQL("DELETE FROM {} WHERE source = %s AND chunk_index = %s").format(ident),
                    stale,
                )

    inserted = sum(1 for c in changed if (c.source, c.chunk_index) not in existing)
    return IngestReport(
        sources=len({c.source for c in chunks}),
        chunks=len(chunks),
        inserted=inserted,
        updated=len(changed) - inserted,
        unchanged=len(chunks) - len(changed),
        deleted=len(stale),
    )


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Ingest policy docs into pgvector + tsvector.")
    parser.add_argument("--dir", type=Path, default=Path(settings.policies_dir))
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--rebuild", action="store_true", help="drop and recreate the table")
    args = parser.parse_args(argv)

    try:
        chunks = load_corpus(args.dir)
        embedder = get_embedder(settings)
        with connect() as conn:
            report = ingest(conn, embedder, chunks, table=args.table, rebuild=args.rebuild)
    except (IngestError, ValueError) as exc:
        print(f"ingest failed: {exc}", file=sys.stderr)
        return 1
    print(f"ingested with {embedder.provider}:{embedder.model} -> {report.model_dump_json()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
