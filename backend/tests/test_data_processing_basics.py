from __future__ import annotations

import py_compile
import sys
import warnings
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from agentic_rag.ingestion.chunker import Chunk, SentenceAwareChunker
from agentic_rag.ingestion.store import HybridStore
from core_pipeline.event_extract_v11 import Event, ExtractionResult
from core_pipeline.document_classifier import DocumentPairClassifier
from core_pipeline.event_coref_cluster import split_broad_semantic_clusters
from scripts.run_event_level_pipeline import rescue_cross_topic_splits
from scripts.train_event_classifier import compute_entity_jaccard


class TestSentenceAwareChunker:
    def test_chinese_sentence_split_without_whitespace(self):
        chunker = SentenceAwareChunker(chunk_size=16, overlap=0, min_chunk_size=1)
        text = "第一句话很重要。第二句话也很关键。第三句话继续补充。"

        chunks = chunker.chunk(text, doc_id="doc-1")

        assert len(chunks) >= 2
        assert all(len(c.text) <= 16 for c in chunks)

    def test_overlap_uses_complete_sentence_units(self):
        chunker = SentenceAwareChunker(chunk_size=24, overlap=12, min_chunk_size=1)
        text = "Alpha one. Beta two. Gamma three. Delta four."

        chunks = chunker.chunk(text, doc_id="doc-2")

        assert len(chunks) >= 2
        assert chunks[1].text.startswith("Beta two.")

    def test_short_text_is_not_dropped(self):
        chunker = SentenceAwareChunker(chunk_size=128, overlap=0, min_chunk_size=64)

        chunks = chunker.chunk("Short text.", doc_id="doc-3")

        assert len(chunks) == 1
        assert chunks[0].text == "Short text."


class TestHybridStore:
    def test_vector_search_with_category_filter(self, tmp_path):
        db_path = tmp_path / "rag.db"
        store = HybridStore(str(db_path))
        try:
            store.upsert_document("doc-a", "A", "src-a", category="alpha")
            store.upsert_document("doc-b", "B", "src-b", category="beta")

            chunks = [
                Chunk(text="alpha text", doc_id="doc-a", chunk_index=0),
                Chunk(text="beta text", doc_id="doc-b", chunk_index=0),
            ]
            vectors = np.asarray(
                [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                dtype=np.float32,
            )
            store.upsert_chunks(chunks, vectors)

            hits = store.vector_search(
                np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
                top_k=5,
                category_filter="alpha",
            )

            assert len(hits) == 1
            assert hits[0]["doc_id"] == "doc-a"
        finally:
            store.close()


class TestPipelineV2Syntax:
    def test_pipeline_v2_compiles(self, tmp_path: Path):
        path = Path(__file__).resolve().parent.parent / "agentic_rag" / "pipeline" / "pipeline_v2.py"
        py_compile.compile(
            str(path),
            cfile=str(tmp_path / "pipeline_v2.pyc"),
            doraise=True,
        )

    def test_training_audit_scripts_compile(self, tmp_path: Path):
        root = REPO_ROOT
        for rel_path in (
            "scripts/l1_review_utils.py",
            "scripts/build_event_pair_dataset.py",
            "scripts/sample_l1_cluster_audit.py",
            "scripts/train_event_classifier.py",
        ):
            source = root / rel_path
            py_compile.compile(
                str(source),
                cfile=str(tmp_path / f"{source.stem}.pyc"),
                doraise=True,
            )


class _DummyScaler:
    def transform(self, features):
        self.last_shape = features.shape
        return features


class _DummyModel:
    def predict_proba(self, features):
        self.last_shape = features.shape
        probs = np.full((features.shape[0],), 0.75, dtype=np.float64)
        return np.column_stack([1.0 - probs, probs])


class TestClassifierFeatureConsistency:
    def test_entity_jaccard_uses_whole_entities(self):
        score = compute_entity_jaccard(
            {"initiator": "United States", "target": "China"},
            {"initiator": "United Kingdom", "target": "Japan"},
        )

        assert score == 0.0

    def test_trigger_overlap_uses_explicit_titles(self):
        clf = DocumentPairClassifier()
        clf._n_features = 9

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("error", DeprecationWarning)
            features = clf._features(
                np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
                np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
                {
                    "title1": "China sanctions exports",
                    "title2": "US sanctions imports",
                    "body1": "irrelevant body",
                    "body2": "different body",
                },
            )

        assert features.shape == (1, 9)
        assert features[0, -1] == 1.0
        assert caught == []

    def test_predict_batch_supports_extended_feature_models(self):
        clf = DocumentPairClassifier()
        clf._model = _DummyModel()
        clf._scaler = _DummyScaler()
        clf._n_features = 9

        probs = clf.predict_batch(
            np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
            np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
        )

        assert probs.shape == (2,)
        assert np.allclose(probs, 0.75)
        assert clf._scaler.last_shape == (2, 9)
        assert clf._model.last_shape == (2, 9)


class TestCrossTopicRescue:
    def test_rescue_merges_tiny_cross_topic_duplicate(self):
        results = [
            ExtractionResult(
                article_id=1,
                published_at="2026-04-11 00:00:00+00:00",
                event=Event(domain="geopolitical", event_type="diplomacy", initiator="Trump", target="Iran"),
                raw_response="",
                parse_success=True,
            ),
            ExtractionResult(
                article_id=2,
                published_at="2026-04-11 00:00:00+00:00",
                event=Event(domain="geopolitical", event_type="diplomacy", initiator="Trump", target="Iran"),
                raw_response="",
                parse_success=True,
            ),
            ExtractionResult(
                article_id=3,
                published_at="2026-04-11 00:00:00+00:00",
                event=Event(domain="geopolitical", event_type="diplomacy", initiator="Trump", target="Iran"),
                raw_response="",
                parse_success=True,
            ),
        ]
        clusters = {
            "0_100": [1, 2],
            "11_200": [3],
        }
        embeddings = {
            1: np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
            2: np.asarray([0.99, 0.01, 0.0], dtype=np.float32),
            3: np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
        }
        titles = {
            1: "Trump says Iran nuclear talks can start soon",
            2: "Trump says Iran nuclear talks can start soon",
            3: "Trump says Iran nuclear talks can start soon",
        }

        merged, merge_count = rescue_cross_topic_splits(clusters, results, embeddings, titles)

        assert merge_count == 1
        assert list(merged.keys()) == ["0_100"]
        assert merged["0_100"] == [1, 2, 3]

    def test_rescue_skips_large_cluster_to_avoid_storyline_overmerge(self):
        results = [
            ExtractionResult(
                article_id=i,
                published_at="2026-04-11 00:00:00+00:00",
                event=Event(domain="geopolitical", event_type="diplomacy", initiator="Trump", target="Iran"),
                raw_response="",
                parse_success=True,
            )
            for i in range(1, 6)
        ]
        clusters = {
            "0_100": [1, 2, 3],
            "11_200": [4, 5],
        }
        embeddings = {
            i: np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
            for i in range(1, 6)
        }
        titles = {i: "Trump says Iran nuclear talks can start soon" for i in range(1, 6)}

        merged, merge_count = rescue_cross_topic_splits(clusters, results, embeddings, titles)

        assert merge_count == 0
        assert merged == clusters


class TestBroadClusterRefinement:
    def test_refinement_splits_same_entity_multi_day_subevents(self):
        results = [
            ExtractionResult(
                article_id=1,
                published_at="2026-03-01 00:00:00+00:00",
                event=Event(domain="geopolitical", event_type="military", initiator="US", target="Iran", trigger_verb="launches strikes"),
                raw_response="",
                parse_success=True,
            ),
            ExtractionResult(
                article_id=2,
                published_at="2026-03-01 00:00:00+00:00",
                event=Event(domain="geopolitical", event_type="military", initiator="US", target="Iran", trigger_verb="launches strikes"),
                raw_response="",
                parse_success=True,
            ),
            ExtractionResult(
                article_id=3,
                published_at="2026-03-02 00:00:00+00:00",
                event=Event(domain="geopolitical", event_type="military", initiator="US", target="Iran", trigger_verb="reports casualties"),
                raw_response="",
                parse_success=True,
            ),
            ExtractionResult(
                article_id=4,
                published_at="2026-03-02 00:00:00+00:00",
                event=Event(domain="geopolitical", event_type="military", initiator="US", target="Iran", trigger_verb="reports casualties"),
                raw_response="",
                parse_success=True,
            ),
            ExtractionResult(
                article_id=5,
                published_at="2026-03-03 00:00:00+00:00",
                event=Event(domain="geopolitical", event_type="military", initiator="US", target="Iran", trigger_verb="announces ceasefire talks"),
                raw_response="",
                parse_success=True,
            ),
            ExtractionResult(
                article_id=6,
                published_at="2026-03-03 00:00:00+00:00",
                event=Event(domain="geopolitical", event_type="military", initiator="US", target="Iran", trigger_verb="announces ceasefire talks"),
                raw_response="",
                parse_success=True,
            ),
        ]
        clusters = {"root": [1, 2, 3, 4, 5, 6]}
        lookup = {row.article_id: row for row in results}
        embeddings = {
            1: np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
            2: np.asarray([0.99, 0.01, 0.0], dtype=np.float32),
            3: np.asarray([0.0, 1.0, 0.0], dtype=np.float32),
            4: np.asarray([0.01, 0.99, 0.0], dtype=np.float32),
            5: np.asarray([0.0, 0.0, 1.0], dtype=np.float32),
            6: np.asarray([0.0, 0.01, 0.99], dtype=np.float32),
        }
        titles = {
            1: "US launches strikes on Iran missile sites",
            2: "US launches strikes on Iran missile sites",
            3: "Casualties reported after US strike on Iran",
            4: "Casualties reported after US strike on Iran",
            5: "US and Iran prepare ceasefire talks",
            6: "US and Iran prepare ceasefire talks",
        }

        refined = split_broad_semantic_clusters(
            clusters,
            lookup,
            embeddings,
            article_titles=titles,
            min_cluster_size=6,
        )

        assert sorted(sorted(members) for members in refined.values()) == [[1, 2], [3, 4], [5, 6]]

    def test_refinement_keeps_duplicate_like_cluster_together(self):
        results = [
            ExtractionResult(
                article_id=i,
                published_at="2026-04-11 00:00:00+00:00",
                event=Event(domain="geopolitical", event_type="diplomacy", initiator="US", target="Iran", trigger_verb="talks collapse"),
                raw_response="",
                parse_success=True,
            )
            for i in range(1, 7)
        ]
        clusters = {"root": [1, 2, 3, 4, 5, 6]}
        lookup = {row.article_id: row for row in results}
        embeddings = {
            1: np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
            2: np.asarray([0.99, 0.01, 0.0], dtype=np.float32),
            3: np.asarray([0.985, 0.015, 0.0], dtype=np.float32),
            4: np.asarray([0.98, 0.02, 0.0], dtype=np.float32),
            5: np.asarray([0.975, 0.025, 0.0], dtype=np.float32),
            6: np.asarray([0.97, 0.03, 0.0], dtype=np.float32),
        }
        titles = {i: "US-Iran peace talks in Islamabad fall through" for i in range(1, 7)}

        refined = split_broad_semantic_clusters(
            clusters,
            lookup,
            embeddings,
            article_titles=titles,
            min_cluster_size=6,
        )

        assert refined == {"root": [1, 2, 3, 4, 5, 6]}

    def test_rescue_requires_same_day_window(self):
        results = [
            ExtractionResult(
                article_id=1,
                published_at="2026-04-11 00:00:00+00:00",
                event=Event(domain="geopolitical", event_type="diplomacy", initiator="Trump", target="Iran"),
                raw_response="",
                parse_success=True,
            ),
            ExtractionResult(
                article_id=2,
                published_at="2026-04-11 00:00:00+00:00",
                event=Event(domain="geopolitical", event_type="diplomacy", initiator="Trump", target="Iran"),
                raw_response="",
                parse_success=True,
            ),
            ExtractionResult(
                article_id=3,
                published_at="2026-04-13 00:00:00+00:00",
                event=Event(domain="geopolitical", event_type="diplomacy", initiator="Trump", target="Iran"),
                raw_response="",
                parse_success=True,
            ),
        ]
        clusters = {
            "0_100": [1, 2],
            "11_200": [3],
        }
        embeddings = {
            1: np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
            2: np.asarray([0.99, 0.01, 0.0], dtype=np.float32),
            3: np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
        }
        titles = {
            1: "Trump says Iran nuclear talks can start soon",
            2: "Trump says Iran nuclear talks can start soon",
            3: "Trump says Iran nuclear talks can start soon",
        }

        merged, merge_count = rescue_cross_topic_splits(clusters, results, embeddings, titles)

        assert merge_count == 0
        assert merged == clusters

    def test_rescue_skips_multi_day_target_cluster(self):
        results = [
            ExtractionResult(
                article_id=1,
                published_at="2026-04-11 00:00:00+00:00",
                event=Event(domain="geopolitical", event_type="diplomacy", initiator="Trump", target="Iran"),
                raw_response="",
                parse_success=True,
            ),
            ExtractionResult(
                article_id=2,
                published_at="2026-04-13 00:00:00+00:00",
                event=Event(domain="geopolitical", event_type="diplomacy", initiator="Trump", target="Iran"),
                raw_response="",
                parse_success=True,
            ),
            ExtractionResult(
                article_id=3,
                published_at="2026-04-11 00:00:00+00:00",
                event=Event(domain="geopolitical", event_type="diplomacy", initiator="Trump", target="Iran"),
                raw_response="",
                parse_success=True,
            ),
        ]
        clusters = {
            "0_100": [1, 2],
            "11_200": [3],
        }
        embeddings = {
            1: np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
            2: np.asarray([0.99, 0.01, 0.0], dtype=np.float32),
            3: np.asarray([1.0, 0.0, 0.0], dtype=np.float32),
        }
        titles = {1: "A", 2: "B", 3: "A"}

        merged, merge_count = rescue_cross_topic_splits(clusters, results, embeddings, titles)

        assert merge_count == 0
        assert merged == clusters
