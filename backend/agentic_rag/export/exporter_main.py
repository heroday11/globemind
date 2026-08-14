"""
CLI：组装 data_fetcher + topology_builder，写出 graph_data.json / clusters_meta.json。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict

BASE_DIR = Path(__file__).resolve().parent.parent

from dotenv import load_dotenv

load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parent / ".env", override=False)

from agentic_rag.db.milvus_store import connect_milvus_collection
from agentic_rag.export import data_fetcher
from agentic_rag.export.topology_builder import build_graph, merge_storyline_titles
from config.settings import graph_output_dir


def save(graph: dict, clusters_meta: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(graph, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    size_mb = out_path.stat().st_size / 1024 / 1024
    meta_path = out_path.parent / "clusters_meta.json"
    meta_path.write_text(
        json.dumps(clusters_meta, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    meta_mb = meta_path.stat().st_size / 1024 / 1024
    print(f"[Output] {meta_path}  entries={len(clusters_meta)}  {meta_mb:.2f} MB")
    print(
        f"[Output] {out_path}  nodes={len(graph['nodes'])}  links={len(graph['links'])}  {size_mb:.2f} MB"
    )


def main() -> None:
    default_out = graph_output_dir() / "graph_data.json"
    parser = argparse.ArgumentParser(description="Export 3d-force-graph JSON")
    parser.add_argument("--out", default=str(default_out), help="输出路径")
    parser.add_argument("--no-milvus", action="store_true", help="跳过 Milvus，仅用 PG cluster_meta 生成骨架图")
    args = parser.parse_args()

    out_path = Path(args.out)

    if args.no_milvus:
        cluster_meta = data_fetcher.fetch_cluster_meta()
        news_cluster_map: Dict[int, int] = {}
        title_map: Dict[int, str] = {}
        coord_map = None
    else:
        col = connect_milvus_collection()
        news_cluster_map = data_fetcher.fetch_news_clusters(col)
        title_map = data_fetcher.fetch_titles(list(news_cluster_map.keys()))
        cluster_meta = data_fetcher.fetch_cluster_meta()
        coord_map = None

    semantic_links = None

    macro_events: Dict = {}
    fine_to_macro: Dict = {}
    try:
        macro_events, fine_to_macro = data_fetcher.load_macro_events_bundle()
    except Exception as e:
        print(f"[Warning] macro_events not available: {e}")

    pg_macro_titles, pg_micro_titles = data_fetcher.fetch_pg_macro_and_micro_titles()
    storyline_titles = merge_storyline_titles(macro_events, pg_macro_titles)

    graph, clusters_meta = build_graph(
        news_cluster_map,
        title_map,
        cluster_meta,
        coord_map=coord_map,
        semantic_links=semantic_links,
        macro_events=macro_events if macro_events else None,
        fine_to_macro=fine_to_macro,
        storyline_titles=storyline_titles,
        micro_titles=pg_micro_titles,
    )
    save(graph, clusters_meta, out_path)
    print("[Done]")


if __name__ == "__main__":
    main()
