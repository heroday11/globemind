#!/usr/bin/env python3
"""Sample current L3/L2 rows and exercise all nine /api/graph endpoints."""

# ruff: noqa: E402 - the script adds backend/ to sys.path before API imports.

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import quote

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.core.db import SessionLocal
from api.main import app


def pick_ids() -> tuple[str | None, str | None, str]:
    db: Session = SessionLocal()
    try:
        row = db.execute(
            text(
                """
                SELECT macro.macro_id, macro.title, member.l2_chain_id
                FROM public.event_l3_macro_events AS macro
                JOIN public.event_l3_macro_members AS member
                  ON member.macro_id = macro.macro_id
                ORDER BY macro.macro_id ASC, member.node_order ASC
                LIMIT 1
                """
            )
        ).mappings().first()
        if not row:
            return None, None, "a"
        macro_id = str(row["macro_id"])
        chain_id = str(row["l2_chain_id"])
        raw_title = (row.get("title") or "").strip()
        title_q = (raw_title[:24] or "a") if raw_title else "a"
        return macro_id, chain_id, title_q
    finally:
        db.close()


async def run() -> int:
    macro_id, chain_id, title_q = pick_ids()
    if macro_id is None or chain_id is None:
        print("SKIP: current L3/L2 hierarchy has no linked rows")
        return 2

    encoded_macro_id = quote(macro_id, safe="")
    encoded_chain_id = quote(chain_id, safe="")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        paths = [
            ("GET", f"/api/graph/macro/{encoded_macro_id}", None),
            ("GET", f"/api/graph/macro/{encoded_macro_id}/briefing", None),
            ("GET", f"/api/graph/macro/{encoded_macro_id}/micros?limit=5&offset=0", None),
            ("GET", f"/api/graph/macro/{encoded_macro_id}/tree?micro_limit=20", None),
            ("GET", f"/api/graph/macros/search?q={quote(title_q)}&limit=5", None),
            (
                "GET",
                "/api/graph/universe?macro_limit=5&micro_per_macro=5&unclustered_limit=0&news_per_micro=0",
                None,
            ),
            ("GET", f"/api/graph/micro/{encoded_chain_id}", None),
            (
                "GET",
                f"/api/graph/micro/{encoded_chain_id}/news?page=1&page_size=5&brief=true",
                None,
            ),
            (
                "POST",
                "/api/graph/micros/news-batch",
                {"event_ids": [chain_id], "limit_per": 5},
            ),
        ]

        ok = 0
        for method, path, body in paths:
            kw = {}
            if body is not None:
                kw["json"] = body
            r = await client.request(method, path, **kw)
            body = r.text[:600]
            try:
                j = r.json()
                extra = f" keys={list(j.keys())[:8]}" if isinstance(j, dict) else ""
            except Exception:
                extra = ""
            status = "OK" if r.status_code == 200 else "FAIL"
            if r.status_code == 200:
                ok += 1
            print(f"[{status}] {r.status_code} {method} {path}{extra}")
            if r.status_code != 200:
                print(f"    body: {body}")

        print(f"\nSampled macro_id={macro_id}, chain_id={chain_id}")
        print(f"通过 {ok}/{len(paths)} 个请求 (HTTP 200)")
        return 0 if ok == len(paths) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
