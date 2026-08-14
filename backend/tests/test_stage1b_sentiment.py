"""Stage 1b 情感分析 — 单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentic_rag.stage1b_sentiment import (
    _hf_sentiment_label_to_score,
    _is_parlasent_model,
    resolve_stage1b_sentiment_model_ref,
)


class TestHfSentimentLabelToScore:
    def test_positive(self):
        assert _hf_sentiment_label_to_score("POSITIVE", 0.95) == pytest.approx(0.95)

    def test_negative(self):
        assert _hf_sentiment_label_to_score("NEGATIVE", 0.85) == pytest.approx(-0.85)

    def test_neutral(self):
        assert _hf_sentiment_label_to_score("NEUTRAL", 0.6) == 0.0

    def test_empty_label(self):
        assert _hf_sentiment_label_to_score("", 0.5) == 0.0

    def test_confidence_clamping(self):
        assert _hf_sentiment_label_to_score("POSITIVE", 1.5) == 1.0
        assert _hf_sentiment_label_to_score("NEGATIVE", -0.5) == 0.0

    def test_label_variants(self):
        assert _hf_sentiment_label_to_score("NEG", 0.7) == pytest.approx(-0.7)
        assert _hf_sentiment_label_to_score("POS", 0.8) == pytest.approx(0.8)
        assert _hf_sentiment_label_to_score("negative", 0.9) == pytest.approx(-0.9)


class TestIsParlasentModel:
    def test_parlasent(self):
        assert _is_parlasent_model("classla/xlm-r-parlasent")
        assert _is_parlasent_model("XLM-R-ParlaSent")

    def test_not_parlasent(self):
        assert not _is_parlasent_model("distilbert-base-uncased")
        assert not _is_parlasent_model("")


@patch("pathlib.Path")
class TestResolveSentimentModelRef:
    def test_env_var_takes_priority(self, mock_path):
        """环境变量优先于本地目录和默认值。"""
        mock_p = MagicMock()
        mock_p.is_dir.return_value = True
        mock_p.exists.return_value = True
        mock_p.resolve.return_value = "/env/path"
        mock_path.return_value = mock_p

        with patch.dict("os.environ", {"STAGE1B_SENTIMENT_MODEL_PATH": "/env/sentiment"}, clear=False):
            result = resolve_stage1b_sentiment_model_ref()
            assert "env" in result

    def test_legacy_dir_fallback(self, mock_path):
        """无环境变量时检查 legacy 目录。"""
        mock_p = MagicMock()
        mock_p.is_dir.return_value = True
        mock_p.exists.return_value = True
        mock_p.resolve.return_value = "/models/sentiment_model"
        mock_path.return_value = mock_p

        with patch.dict("os.environ", {}, clear=True):
            result = resolve_stage1b_sentiment_model_ref()
            assert "sentiment_model" in result

    def test_hub_default(self, mock_path):
        """无环境变量且无本地目录时返回 Hub 默认值。"""
        mock_p = MagicMock()
        mock_p.is_dir.return_value = False
        mock_path.return_value = mock_p

        with patch.dict("os.environ", {}, clear=True):
            result = resolve_stage1b_sentiment_model_ref()
            assert result == "classla/xlm-r-parlasent"
