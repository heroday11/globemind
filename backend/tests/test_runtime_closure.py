from __future__ import annotations

import importlib


def test_story_pipeline_runtime_closure_imports() -> None:
    event_coref = importlib.import_module("core_pipeline.event_coref_cluster")
    story_tree = importlib.import_module("agentic_rag.pipeline.story_tree_builder")

    assert callable(event_coref._canonical_entity)
    assert isinstance(story_tree.SYMMETRIC_EVENT_TYPES, frozenset)
