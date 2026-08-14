#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
微观分析阶段的第三方库噪声警告过滤（GLiNER / transformers / huggingface_hub）。

恢复全部警告：环境变量 ANALYSIS_VERBOSE_WARNINGS=1
"""
from __future__ import annotations

import builtins
import os


def analysis_verbose_warnings() -> bool:
    return os.getenv("ANALYSIS_VERBOSE_WARNINGS", "").strip().lower() in ("1", "true", "yes", "on")


def apply_run_analysis_warning_filters() -> None:
    """在加载 Embedder / GLiNER 之前调用。"""
    if analysis_verbose_warnings():
        return
    import warnings

    # 减少 huggingface_hub「Fetching N files」等进度条（需在首次下载前设置）
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    # 降低 transformers  tokenizer 的 INFO/部分告警噪声（含 truncate 提示）
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

    # GLiNER：长文本截断到 384
    warnings.filterwarnings(
        "ignore",
        message=r".*Sentence of length \d+ has been truncated to 384.*",
        category=builtins.UserWarning,
    )
    # transformers tokenization
    warnings.filterwarnings(
        "ignore",
        message=r".*Asking to truncate to max_length but no maximum length is provided.*",
        category=builtins.UserWarning,
    )
    # 部分版本以非 UserWarning 或未带 category 形式发出
    warnings.filterwarnings(
        "ignore",
        message=r".*Asking to truncate to max_length.*",
    )
    warnings.filterwarnings(
        "ignore",
        message=r".*byte fallback option which is not implemented in the fast tokenizers.*",
        category=builtins.UserWarning,
    )
    # huggingface_hub（模型文件下载）
    warnings.filterwarnings(
        "ignore",
        message=r".*resume_download.*",
        category=builtins.FutureWarning,
    )
