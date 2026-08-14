"""
Document pair classifier — replaces fixed cosine threshold in L1 clustering.

Trains on existing L1 clusters (self-supervised):
  - Positive pairs: articles in the same cluster
  - Negative pairs: articles in different clusters but same entity pair

Usage:
    from core_pipeline.document_classifier import DocumentPairClassifier
    clf = DocumentPairClassifier()
    clf.load()  # load pre-trained model
    score = clf.predict(emb1, emb2)  # 0-1 probability
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("document_classifier")

_MODEL_DIR = Path(__file__).resolve().parent.parent / "data" / "models"
_MODEL_PATH = _MODEL_DIR / "document_classifier_mlp.joblib"
_NULL_ENTITY_VALUES = {"", "null", "none", "unknown"}
_TITLE_STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at",
    "to", "for", "of", "with", "by", "from", "as", "is", "are",
    "was", "were", "be", "been", "have", "has", "had", "do",
    "does", "did", "will", "would", "could", "should", "may",
    "might", "shall", "can", "its", "it", "this", "that",
    "these", "those", "we", "you", "they", "he", "she",
    "not", "no", "nor", "so", "up", "down", "out", "off",
    "over", "under", "again", "further", "then", "once",
    "here", "there", "all", "each", "every", "both", "few",
    "more", "most", "other", "some", "such", "only", "own",
    "same", "than", "too", "very", "just", "also", "about",
    "above", "after", "how", "what", "when", "where", "which",
    "who", "whom", "why",
}


class DocumentPairClassifier:
    """Document pair classifier using learned embeddings + features.

    Compares two BGE-M3 document embeddings and predicts the probability
    that they describe the same event (0-1 scale).

    Basic features (3-d):
      - Cosine similarity
      - Euclidean distance
      - Max component-wise difference

    Extended features (9-d, used by event_classifier_logreg):
      - cosine_sim, euclidean_dist, max_component_diff
      - time_delta_days (normalized)
      - entity_jaccard
      - source_same
      - event_type_exact
      - length_ratio
      - trigger_overlap
    """

    def __init__(self):
        self._model = None
        self._scaler = None
        self._n_features = 3  # default for basic doc classifier

    def load(self, path: Optional[str] = None) -> bool:
        """Load pre-trained model from disk.

        Auto-detects feature count from model weights. If the model expects
        9 features, also loads the corresponding scaler.

        Returns True if model loaded successfully, False otherwise.
        """
        model_path = Path(path) if path else _MODEL_PATH
        if not model_path.exists():
            logger.warning("Classifier model not found at %s", model_path)
            return False

        try:
            import joblib
            self._model = joblib.load(str(model_path))
            logger.info("Loaded classifier from %s", model_path)

            # Detect feature count
            if hasattr(self._model, 'coef_'):
                self._n_features = self._model.coef_.shape[1]
            elif hasattr(self._model, 'coefs_'):
                self._n_features = self._model.coefs_[0].shape[0]
            else:
                self._n_features = 3

            # Load scaler for 9-feature event classifier
            if self._n_features >= 9:
                scaler_path = model_path.parent / "event_classifier_scaler.joblib"
                if scaler_path.exists():
                    self._scaler = joblib.load(str(scaler_path))
                    logger.info("Loaded scaler from %s (n_features=%d)",
                                scaler_path, self._n_features)
                else:
                    logger.warning("9-feature model but no scaler at %s", scaler_path)

            return True
        except Exception as e:
            logger.warning("Failed to load classifier: %s", e)
            return False

    @property
    def available(self) -> bool:
        """Whether a trained model is loaded and ready."""
        return self._model is not None

    def _entity_set(self, event) -> set[str]:
        entities: set[str] = set()
        for value in (event.initiator, event.target):
            cleaned = str(value or "").strip().lower()
            if cleaned and cleaned not in _NULL_ENTITY_VALUES:
                entities.add(cleaned)
        return entities

    def _title_tokens(self, title: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9']+", title.lower())
            if len(token) > 2 and token not in _TITLE_STOP_WORDS
        }

    def _batch_features(self, embs1: np.ndarray, embs2: np.ndarray) -> np.ndarray:
        features = np.column_stack([
            np.sum(embs1 * embs2, axis=1),
            np.sqrt(np.sum((embs1 - embs2) ** 2, axis=1)),
            np.max(np.abs(embs1 - embs2), axis=1),
        ])
        if self._n_features <= 3:
            return features

        # Batch mode has no per-pair metadata; pad the learned feature space
        # with neutral defaults so extended models still infer safely.
        neutral = np.zeros((len(embs1), self._n_features - 3), dtype=np.float64)
        if self._n_features >= 8:
            neutral[:, 4] = 1.0  # length_ratio default
        features = np.hstack([features, neutral])
        if self._scaler is not None:
            features = self._scaler.transform(features)
        return features

    def _features(self, emb1: np.ndarray, emb2: np.ndarray,
                  context: Optional[dict] = None) -> np.ndarray:
        """Extract features from a pair of embeddings.

        Args:
            emb1: BGE-M3 embedding of first article (1024-d)
            emb2: BGE-M3 embedding of second article (1024-d)
            context: Optional dict with article data for extended features.
                Keys: 'article1', 'article2' (ExtractionResult),
                      'body1', 'body2' (str),
                      'source1', 'source2' (str)

        Returns:
            Feature vector of shape (1, n_features).
        """
        cosine = float(np.dot(emb1, emb2))
        euclidean = float(np.sqrt(np.sum((emb1 - emb2) ** 2)))
        max_diff = float(np.max(np.abs(emb1 - emb2)))

        if self._n_features <= 3:
            return np.asarray([cosine, euclidean, max_diff], dtype=np.float64).reshape(1, -1)

        # ── Extended 9-feature set for event_classifier ──
        feat = [cosine, euclidean, max_diff]

        # 4. time_delta_days (normalized log1p)
        time_delta = 0
        if context:
            a1 = context.get('article1')
            a2 = context.get('article2')
            if a1 and a2:
                from core_pipeline.event_coref_cluster import _parse_dt
                dt1 = _parse_dt(a1.published_at)
                dt2 = _parse_dt(a2.published_at)
                if dt1 is not None and dt2 is not None:
                    time_delta = abs((dt1 - dt2).days)
        td_norm = np.log1p(min(time_delta, 200)) / np.log1p(200)
        feat.append(td_norm)

        # 5. entity_jaccard
        ej = 0.0
        if context:
            a1 = context.get('article1')
            a2 = context.get('article2')
            if a1 and a1.event and a2 and a2.event:
                set1 = self._entity_set(a1.event)
                set2 = self._entity_set(a2.event)
                if set1 and set2:
                    ej = len(set1 & set2) / len(set1 | set2)
        feat.append(ej)

        # 6. source_same
        src_same = 0.0
        if context:
            s1 = context.get('source1', '')
            s2 = context.get('source2', '')
            if s1 and s2 and s1 == s2:
                src_same = 1.0
        feat.append(src_same)

        # 7. event_type_exact
        et_exact = 0.0
        if context:
            a1 = context.get('article1')
            a2 = context.get('article2')
            if a1 and a1.event and a2 and a2.event:
                if a1.event.event_type == a2.event.event_type:
                    et_exact = 1.0
        feat.append(et_exact)

        # 8. length_ratio
        lr = 1.0
        if context:
            body1 = context.get('body1', '')
            body2 = context.get('body2', '')
            len1 = len(body1)
            len2 = len(body2)
            if max(len1, len2) > 0:
                lr = min(len1, len2) / max(len1, len2)
        feat.append(lr)

        # 9. trigger_overlap (shared words in titles)
        to_val = 0
        if context:
            title1 = context.get("title1", "")
            title2 = context.get("title2", "")
            if not title1 or not title2:
                body1 = context.get("body1", "")
                body2 = context.get("body2", "")
                title1 = title1 or (body1.split(".")[0] if body1 else "")
                title2 = title2 or (body2.split(".")[0] if body2 else "")
            to_val = len(self._title_tokens(title1) & self._title_tokens(title2))
        feat.append(float(to_val))

        result = np.array(feat, dtype=np.float64).reshape(1, -1)

        # Apply scaler if available
        if self._scaler is not None:
            result = self._scaler.transform(result)

        return result

    def predict(self, emb1: np.ndarray, emb2: np.ndarray,
                **context) -> float:
        """Predict probability that two articles describe the same event.

        Args:
            emb1: BGE-M3 embedding of first article (1024-d)
            emb2: BGE-M3 embedding of second article (1024-d)
            **context: Optional kwargs for extended features:
                article1, article2 (ExtractionResult),
                body1, body2 (str article texts),
                source1, source2 (str source names).

        Returns:
            Probability (0-1) that they are the same event.
            Returns cosine similarity if model is not loaded.
        """
        if not self.available:
            # Fallback to cosine similarity
            cosine = float(np.dot(emb1, emb2))
            return max(0.0, min(1.0, cosine))

        feat = self._features(emb1, emb2, context)
        prob = self._model.predict_proba(feat)[0, 1]
        return float(prob)

    def predict_batch(
        self, embs1: np.ndarray, embs2: np.ndarray
    ) -> np.ndarray:
        """Predict probabilities for a batch of pairs."""
        if not self.available:
            cosines = np.sum(embs1 * embs2, axis=1)
            return np.clip(cosines, 0.0, 1.0)

        features = self._batch_features(embs1, embs2)
        return self._model.predict_proba(features)[:, 1]


# Singleton for easy reuse
_classifier: Optional[DocumentPairClassifier] = None


def get_classifier() -> DocumentPairClassifier:
    """Get or create the global classifier singleton."""
    global _classifier
    if _classifier is None:
        _classifier = DocumentPairClassifier()
        _classifier.load()
    return _classifier


def predict(emb1: np.ndarray, emb2: np.ndarray) -> float:
    """Convenience function: predict same-event probability."""
    return get_classifier().predict(emb1, emb2)


if __name__ == "__main__":
    # Quick test
    clf = DocumentPairClassifier()
    loaded = clf.load()
    print(f"Model loaded: {loaded}")

    # Test with random embeddings
    e1 = np.random.randn(1024).astype(np.float32)
    e1 = e1 / np.linalg.norm(e1)
    e2 = np.random.randn(1024).astype(np.float32)
    e2 = e2 / np.linalg.norm(e2)
    e3 = e1.copy()  # identical

    print(f"Random pair score: {clf.predict(e1, e2):.3f}")
    print(f"Identical pair score: {clf.predict(e1, e3):.3f}")
    print("✅ DocumentPairClassifier ready")
