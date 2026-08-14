#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
from db_runtime_config import require_database_password
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import normalize as sk_normalize


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = PROJECT_ROOT / "data" / "analysis" / "l1_lite_dense_test"

STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "as",
    "by",
    "from",
    "at",
    "after",
    "before",
    "over",
    "under",
    "new",
    "news",
    "says",
    "say",
    "said",
    "live",
    "updates",
    "latest",
    "opinion",
    "analysis",
    "video",
    "photos",
}


@dataclass
class Article:
    id: int
    title: str
    body: str
    published_at: datetime
    source_domain: str
    language: str | None


class UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.size = [1] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test a no-LLM L1-lite story clustering method on a dense time window."
    )
    parser.add_argument("--host", default="192.168.207.171")
    parser.add_argument("--port", type=int, default=54333)
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--dbname", default="news")
    parser.add_argument("--start", default="2026-06-19T09:00:00+00:00")
    parser.add_argument("--end", default="2026-06-19T15:00:00+00:00")
    parser.add_argument("--language", default="en")
    parser.add_argument("--limit", type=int, default=1500)
    parser.add_argument("--neighbors", type=int, default=12)
    parser.add_argument("--min-sim", type=float, default=0.68)
    parser.add_argument("--min-title-token-jaccard", type=float, default=0.18)
    parser.add_argument("--same-source-min-title-jaccard", type=float, default=0.28)
    parser.add_argument("--same-source-min-sim", type=float, default=0.84)
    parser.add_argument("--max-gap-hours", type=float, default=30.0)
    parser.add_argument("--max-cluster-size", type=int, default=20)
    parser.add_argument(
        "--text-mode",
        choices=["title", "title_lead"],
        default="title",
        help="Use title-only vectors by default to avoid site-template body contamination.",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args()


def connect(args: argparse.Namespace) -> Any:
    return psycopg2.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=require_database_password(),
        dbname=args.dbname,
        connect_timeout=20,
    )


def fetch_articles(args: argparse.Namespace) -> list[Article]:
    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    lang_filter = ""
    params: list[Any] = [start, end]
    if args.language:
        lang_filter = "AND n.language = %s"
        params.append(args.language)
    params.append(args.limit)

    conn = connect(args)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT n.id,
                       COALESCE(n.title, '') AS title,
                       COALESCE(n.body, '') AS body,
                       n.published_at,
                       COALESCE(ms.domain, '') AS source_domain,
                       n.language
                FROM public.news n
                LEFT JOIN public.media_source ms ON ms.id = n.media_source_id
                LEFT JOIN public.news_l1_prep f ON f.news_id = n.id
                WHERE n.published_at >= %s
                  AND n.published_at < %s
                  {lang_filter}
                  AND COALESCE(n.title, '') <> ''
                  AND COALESCE(n.body, '') <> ''
                  AND COALESCE(f.text_quality_flag, 'ok') = 'ok'
                ORDER BY n.published_at, n.id
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        Article(
            id=int(row[0]),
            title=str(row[1] or ""),
            body=str(row[2] or ""),
            published_at=row[3],
            source_domain=str(row[4] or ""),
            language=row[5],
        )
        for row in rows
    ]


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def lead(value: str, chars: int = 500) -> str:
    return clean_text(value)[:chars]


def doc_text(article: Article, text_mode: str) -> str:
    title = clean_text(article.title)
    if text_mode == "title":
        return title
    return f"{title}. {lead(article.body)}"


def title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9][a-z0-9'-]{2,}", title.lower())
    return {word.strip("'-") for word in words if word not in STOPWORDS}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def time_gap_hours(left: Article, right: Article) -> float:
    return abs((left.published_at - right.published_at).total_seconds()) / 3600.0


def build_matrix(texts: list[str]) -> sparse.csr_matrix:
    word_vec = TfidfVectorizer(
        lowercase=True,
        strip_accents="unicode",
        ngram_range=(1, 2),
        min_df=1,
        max_df=0.85,
        stop_words="english",
        sublinear_tf=True,
        max_features=60000,
    )
    char_vec = TfidfVectorizer(
        lowercase=True,
        strip_accents="unicode",
        analyzer="char_wb",
        ngram_range=(4, 6),
        min_df=1,
        max_df=0.90,
        sublinear_tf=True,
        max_features=90000,
    )
    word = word_vec.fit_transform(texts)
    char = char_vec.fit_transform(texts)
    matrix = sparse.hstack([word * 0.7, char * 0.3], format="csr")
    return sk_normalize(matrix, norm="l2", axis=1, copy=False)


def cluster_articles(articles: list[Article], args: argparse.Namespace) -> dict[int, list[int]]:
    texts = [doc_text(article, args.text_mode) for article in articles]
    matrix = build_matrix(texts)
    n_neighbors = min(max(2, args.neighbors + 1), len(articles))
    nn = NearestNeighbors(n_neighbors=n_neighbors, metric="cosine", algorithm="brute")
    nn.fit(matrix)
    distances, indices = nn.kneighbors(matrix)
    tokens = [title_tokens(article.title) for article in articles]
    uf = UnionFind(len(articles))

    for i, row in enumerate(indices):
        for pos, j in enumerate(row):
            if i == j:
                continue
            sim = 1.0 - float(distances[i][pos])
            if sim < args.min_sim:
                continue
            if time_gap_hours(articles[i], articles[j]) > args.max_gap_hours:
                continue
            token_j = jaccard(tokens[i], tokens[j])
            if token_j < args.min_title_token_jaccard and sim < max(0.82, args.min_sim + 0.10):
                continue
            if articles[i].source_domain == articles[j].source_domain:
                if token_j < args.same_source_min_title_jaccard and sim < args.same_source_min_sim:
                    continue
            uf.union(i, int(j))

    clusters: dict[int, list[int]] = {}
    for i in range(len(articles)):
        clusters.setdefault(uf.find(i), []).append(i)

    # Split suspicious giant clusters by refusing to report them as merged clusters.
    out: dict[int, list[int]] = {}
    next_id = len(articles)
    for root, members in clusters.items():
        if len(members) <= args.max_cluster_size:
            out[root] = members
            continue
        for member in members:
            out[next_id] = [member]
            next_id += 1
    return out


def pairwise_title_sim(articles: list[Article], members: list[int]) -> tuple[float, float]:
    if len(members) < 2:
        return 1.0, 1.0
    titles = [clean_text(articles[i].title) for i in members]
    matrix = TfidfVectorizer(
        lowercase=True,
        strip_accents="unicode",
        analyzer="char_wb",
        ngram_range=(4, 6),
        min_df=1,
    ).fit_transform(titles)
    sims = cosine_similarity(matrix)
    vals = [
        float(sims[i, j])
        for i in range(len(members))
        for j in range(i + 1, len(members))
    ]
    return sum(vals) / len(vals), min(vals)


def summarize(articles: list[Article], clusters: dict[int, list[int]]) -> dict[str, Any]:
    sizes = [len(members) for members in clusters.values()]
    merged = [members for members in clusters.values() if len(members) >= 2]
    cross_source = [
        members
        for members in merged
        if len({articles[i].source_domain for i in members}) >= 2
    ]
    title_sims = [pairwise_title_sim(articles, members)[0] for members in merged]
    min_title_sims = [pairwise_title_sim(articles, members)[1] for members in merged]
    return {
        "articles": len(articles),
        "clusters": len(clusters),
        "singleton_clusters": sum(1 for size in sizes if size == 1),
        "merged_clusters": len(merged),
        "merged_articles": sum(len(members) for members in merged),
        "cross_source_clusters": len(cross_source),
        "max_cluster_size": max(sizes) if sizes else 0,
        "avg_merged_cluster_size": round(
            sum(len(members) for members in merged) / len(merged), 2
        )
        if merged
        else 0,
        "avg_pairwise_title_char_sim": round(sum(title_sims) / len(title_sims), 3)
        if title_sims
        else None,
        "min_pairwise_title_char_sim_p10": round(sorted(min_title_sims)[max(0, math.floor(len(min_title_sims) * 0.1) - 1)], 3)
        if min_title_sims
        else None,
    }


def representative_title(articles: list[Article], members: list[int]) -> str:
    return max((clean_text(articles[i].title) for i in members), key=len)


def write_outputs(
    articles: list[Article],
    clusters: dict[int, list[int]],
    summary: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    prefix = (
        f"{args.start.replace(':', '').replace('+', 'z')}_"
        f"{args.end.replace(':', '').replace('+', 'z')}_"
        f"{args.language or 'all'}_{args.text_mode}_"
        f"s{args.min_sim:.2f}_j{args.min_title_token_jaccard:.2f}"
    )
    json_path = args.out_dir / f"{prefix}_clusters.jsonl"
    md_path = args.out_dir / f"{prefix}_sample.md"
    summary_path = args.out_dir / f"{prefix}_summary.json"

    sorted_clusters = sorted(
        clusters.items(),
        key=lambda item: (-len(item[1]), representative_title(articles, item[1]).lower()),
    )
    with json_path.open("w", encoding="utf-8") as handle:
        for cluster_id, members in sorted_clusters:
            if len(members) < 2:
                continue
            row = {
                "cluster_id": str(cluster_id),
                "size": len(members),
                "source_count": len({articles[i].source_domain for i in members}),
                "representative_title": representative_title(articles, members),
                "members": [
                    {
                        "news_id": articles[i].id,
                        "published_at": articles[i].published_at.isoformat(),
                        "source_domain": articles[i].source_domain,
                        "title": clean_text(articles[i].title),
                    }
                    for i in sorted(members, key=lambda idx: articles[idx].published_at)
                ],
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    with md_path.open("w", encoding="utf-8") as handle:
        handle.write("# L1-lite Dense Window Sample\n\n")
        handle.write("```json\n")
        handle.write(json.dumps(summary, ensure_ascii=False, indent=2))
        handle.write("\n```\n\n")
        shown = 0
        for _, members in sorted_clusters:
            if len(members) < 2:
                continue
            shown += 1
            avg_sim, min_sim = pairwise_title_sim(articles, members)
            handle.write(
                f"## Cluster {shown}: size={len(members)}, sources="
                f"{len({articles[i].source_domain for i in members})}, "
                f"avg_title_sim={avg_sim:.3f}, min_title_sim={min_sim:.3f}\n\n"
            )
            for i in sorted(members, key=lambda idx: articles[idx].published_at):
                a = articles[i]
                handle.write(
                    f"- `{a.published_at.isoformat()}` `{a.source_domain}` "
                    f"`{a.id}` {clean_text(a.title)}\n"
                )
            handle.write("\n")
            if shown >= 40:
                break

    summary.update(
        {
            "window_start": args.start,
            "window_end": args.end,
            "language": args.language,
            "min_sim": args.min_sim,
            "min_title_token_jaccard": args.min_title_token_jaccard,
            "same_source_min_title_jaccard": args.same_source_min_title_jaccard,
            "same_source_min_sim": args.same_source_min_sim,
            "max_gap_hours": args.max_gap_hours,
            "text_mode": args.text_mode,
            "clusters_jsonl": str(json_path),
            "sample_md": str(md_path),
        }
    )
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    args = parse_args()
    articles = fetch_articles(args)
    if len(articles) < 2:
        raise SystemExit("not enough articles")
    clusters = cluster_articles(articles, args)
    summary = summarize(articles, clusters)
    write_outputs(articles, clusters, summary, args)


if __name__ == "__main__":
    main()
