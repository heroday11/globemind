#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
将 Stage1b 默认情感模型下载到本地目录，便于离线或固定路径。

  .venv\\Scripts\\python.exe -m agentic_rag.tools.download_sentiment_model
  .venv\\Scripts\\python.exe -m agentic_rag.tools.download_sentiment_model --out D:/models/sentiment_model

下载完成后在 .env 中设置：
  STAGE1B_SENTIMENT_MODEL_PATH=D:/models/sentiment_model
"""
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(description="下载 Stage1b 情感模型到本地目录")
    p.add_argument(
        "--repo",
        default="distilbert-base-uncased-finetuned-sst-2-english",
        help="Hugging Face 模型 ID",
    )
    p.add_argument(
        "--out",
        default=r"D:\models\sentiment_model",
        help="本地输出目录（将创建）",
    )
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=args.repo,
        local_dir=str(out),
        local_dir_use_symlinks=False,
    )
    print(f"[download_sentiment_model] 完成: {out.resolve()}")
    print(f"[download_sentiment_model] 请在 .env 设置: STAGE1B_SENTIMENT_MODEL_PATH={out}")


if __name__ == "__main__":
    main()
