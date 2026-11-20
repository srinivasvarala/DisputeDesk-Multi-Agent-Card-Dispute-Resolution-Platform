"""Checkpoint serializer that explicitly allows the types stored in graph state.

LangGraph will stop deserializing unregistered classes from checkpoints, so every Pydantic
model and enum that can appear in `DisputeState` (including nested ones) is registered here.
The set is found by walking the state's field types, so adding a field does not need an edit
here; `tests/unit/test_checkpoint_serde.py` round-trips a full state to prove it.
"""

from __future__ import annotations

import enum
import types
import typing
from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

from disputedesk.graph.state import DisputeState


def _classes_in(annotation: Any, seen: set[type]) -> None:
    for arg in typing.get_args(annotation):
        _classes_in(arg, seen)
    if isinstance(annotation, types.UnionType | typing._GenericAlias):  # type: ignore[attr-defined]
        return
    if isinstance(annotation, type) and annotation not in seen:
        if issubclass(annotation, BaseModel):
            seen.add(annotation)
            for field in annotation.model_fields.values():
                _classes_in(field.annotation, seen)
        elif issubclass(annotation, enum.Enum):
            seen.add(annotation)


def checkpoint_types() -> list[type]:
    seen: set[type] = set()
    _classes_in(DisputeState, seen)
    return sorted(seen, key=lambda c: (c.__module__, c.__qualname__))


def checkpoint_serde() -> JsonPlusSerializer:
    allowed = [(c.__module__, c.__qualname__) for c in checkpoint_types()]
    return JsonPlusSerializer(allowed_msgpack_modules=allowed)
