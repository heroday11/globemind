"""GLiNER entity extractor — 单元测试（mock 底层模型）。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentic_rag.gliner_extractor import GLiNEREntityExtractor


class TestGLiNEREntityExtractor:
    """不加载真实模型，mock 验证逻辑。"""

    def test_extract_empty_on_null_model(self):
        """unload_model 后 extract 返回空列表。"""
        ext = GLiNEREntityExtractor.__new__(GLiNEREntityExtractor)
        ext._model = None
        ext._labels = ["person"]
        assert ext.extract("hello world") == []

    def test_extract_empty_text(self):
        """空文本返回空列表。"""
        ext = GLiNEREntityExtractor.__new__(GLiNEREntityExtractor)
        ext._model = MagicMock()
        ext._model.predict_entities.return_value = []
        ext._labels = ["person"]
        assert ext.extract("") == []

    def test_extract_deduplicates(self):
        """重复实体去重。"""
        ext = GLiNEREntityExtractor.__new__(GLiNEREntityExtractor)
        ext._model = MagicMock()
        ext._model.predict_entities.return_value = [
            {"text": "China", "label": "location"},
            {"text": "China", "label": "location"},
            {"text": "US", "label": "location"},
        ]
        ext._labels = ["person", "location", "organization"]
        result = ext.extract("China and US")
        assert result == ["China", "US"]

    def test_extract_truncates_at_80(self):
        """实体列表上限 80 条。"""
        ext = GLiNEREntityExtractor.__new__(GLiNEREntityExtractor)
        ext._model = MagicMock()
        ext._model.predict_entities.return_value = [
            {"text": f"E{i}"} for i in range(100)
        ]
        ext._labels = ["person"]
        result = ext.extract("x" * 1000)
        assert len(result) <= 80

    def test_extract_handles_dict_or_str_preds(self):
        """兼容 dict 和非 dict 预测结果。"""
        ext = GLiNEREntityExtractor.__new__(GLiNEREntityExtractor)
        ext._model = MagicMock()
        ext._model.predict_entities.return_value = [
            {"text": "China", "label": "location"},
            "plain string",
            {"entity": "entity_field"},
        ]
        ext._labels = ["person", "location", "organization"]
        result = ext.extract("test")
        assert "China" in result
        assert "entity_field" in result

    def test_unload_model(self):
        """unload_model 释放模型引用。"""
        ext = GLiNEREntityExtractor.__new__(GLiNEREntityExtractor)
        ext._model = MagicMock()
        ext.unload_model()
        assert ext._model is None

    def test_entities_from_preds(self):
        """_entities_from_preds 静态方法正确提取。"""
        preds = [
            {"text": "China"},
            {"text": "China"},
            {"text": "US"},
        ]
        result = GLiNEREntityExtractor._entities_from_preds(preds)
        assert result == ["China", "US"]

    def test_entities_from_preds_empty(self):
        assert GLiNEREntityExtractor._entities_from_preds([]) == []

    def test_extract_batch_all_empty_on_null_model(self):
        """model 为 None 时 extract_batch 返回对等的空列表。"""
        ext = GLiNEREntityExtractor.__new__(GLiNEREntityExtractor)
        ext._model = None
        ext._labels = ["person"]
        assert ext.extract_batch(["a", "b"]) == [[], []]

    def test_extract_batch_empty_input(self):
        """空输入列表返回空。"""
        ext = GLiNEREntityExtractor.__new__(GLiNEREntityExtractor)
        ext._model = MagicMock()
        ext._labels = ["person"]
        assert ext.extract_batch([]) == []
