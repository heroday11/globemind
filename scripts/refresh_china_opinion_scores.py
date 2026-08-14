#!/usr/bin/env python3
"""Refresh the materialized China opinion score window."""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text

from api.core.environment import discard_plaintext_database_environment, load_environment


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14, help="window length when --start-date is omitted")
    parser.add_argument("--start-date", type=parse_date)
    parser.add_argument("--end-date", type=parse_date)
    parser.add_argument("--force", action="store_true", help="bypass the in-process refresh throttle")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.days < 1 or args.days > 900:
        raise SystemExit("--days must be between 1 and 900")

    load_environment()
    discard_plaintext_database_environment()

    from api.core.db import DB_HOST, DB_NAME, DB_PORT, DB_USER, SessionLocal
    from api.routes.opinion_v2 import METHOD_VERSION, _current_db_date, _refresh_scores

    with SessionLocal() as db:
        current_date = _current_db_date(db)
        end_d = args.end_date or current_date
        start_d = args.start_date or (end_d - timedelta(days=args.days - 1))
        if start_d > end_d:
            raise SystemExit("--start-date must not be after --end-date")
        if end_d > current_date:
            raise SystemExit("--end-date must not be in the future")

        _refresh_scores(db, start_d, end_d, force=args.force)
        row: dict[str, Any] = dict(
            db.execute(
                text(
                    """
                    SELECT
                        max(published_date) AS latest_score_date,
                        count(*) FILTER (WHERE published_date BETWEEN :start_d AND :end_d) AS scored_count,
                        count(*) FILTER (
                            WHERE published_date BETWEEN :start_d AND :end_d
                              AND relevance_score >= 0.4
                        ) AS scored_relevant
                    FROM public.china_opinion_article_scores
                    """
                ),
                {"start_d": start_d, "end_d": end_d},
            )
            .mappings()
            .first()
            or {}
        )

    payload = {
        "ok": True,
        "db": f"{DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}",
        "start_date": start_d.isoformat(),
        "end_date": end_d.isoformat(),
        "method_version": METHOD_VERSION,
        "latest_score_date": row.get("latest_score_date").isoformat()
        if row.get("latest_score_date")
        else None,
        "scored_count": int(row.get("scored_count") or 0),
        "scored_relevant": int(row.get("scored_relevant") or 0),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
