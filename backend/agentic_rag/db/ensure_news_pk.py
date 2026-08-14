#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
确保 public.news(id) 可作为外键引用目标（PRIMARY KEY 或 UNIQUE）。

PostgreSQL 要求 REFERENCES news(id) 的目标列必须有主键或唯一约束；
若爬虫库建表时未加主键，此处会在无重复 id 的前提下补充 PRIMARY KEY (id)。
"""
from __future__ import annotations

from typing import Callable


def _has_unique_on_id_only(cols_by_constraint: list[tuple[str, list[str]]]) -> bool:
    for _name, cols in cols_by_constraint:
        if cols == ["id"]:
            return True
    return False


def _fetch_pk_and_unique_columns(cur) -> tuple[list[tuple[str, list[str]]], list[tuple[str, list[str]]]]:
    """返回 (主键约束列列表, 唯一约束列列表)，每项为 (constraint_name, [col, ...])。"""
    cur.execute(
        """
        SELECT c.conname, c.contype,
               array_agg(a.attname ORDER BY u.ord) AS cols
        FROM pg_constraint c
        JOIN pg_class t ON c.conrelid = t.oid
        JOIN pg_namespace n ON t.relnamespace = n.oid
        JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS u(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = u.attnum
        WHERE n.nspname = 'public' AND t.relname = 'news'
          AND c.contype IN ('p', 'u')
        GROUP BY c.conname, c.contype, c.oid
        """
    )
    rows = cur.fetchall()
    pks: list[tuple[str, list[str]]] = []
    uniques: list[tuple[str, list[str]]] = []
    for conname, contype, cols in rows:
        if not cols:
            continue
        if contype == "p":
            pks.append((conname, list(cols)))
        else:
            uniques.append((conname, list(cols)))
    return pks, uniques


def ensure_news_id_referenced_unique(
    cur,
    log: Callable[..., None] | None = None,
) -> None:
    """
    若无单列 id 的主键/唯一约束，则在数据允许时执行
    ALTER TABLE news ADD CONSTRAINT news_pkey PRIMARY KEY (id)。

    Raises:
        RuntimeError: 表不存在、id 重复、存在 NULL id、或无法自动修复的约束冲突。
    """
    _log: Callable[..., None] = log or print

    cur.execute(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'news'
        """
    )
    if cur.fetchone() is None:
        raise RuntimeError("public.news 不存在，无法创建 news_analysis 外键。")

    cur.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'news' AND column_name = 'id'
        """
    )
    if cur.fetchone() is None:
        raise RuntimeError("public.news 缺少 id 列。")

    pks, uniques = _fetch_pk_and_unique_columns(cur)
    if _has_unique_on_id_only([(n, c) for n, c in pks]) or _has_unique_on_id_only(
        [(n, c) for n, c in uniques]
    ):
        _log("[Schema] news(id) 已有主键或唯一约束，外键可用。")
        return

    cur.execute("SELECT COUNT(*) FROM news WHERE id IS NULL")
    (null_cnt,) = cur.fetchone()
    if null_cnt and int(null_cnt) > 0:
        raise RuntimeError(
            f"news.id 存在 {null_cnt} 条 NULL，无法添加主键/唯一；请先清洗数据。"
        )

    cur.execute(
        """
        SELECT id, COUNT(*) AS c FROM news GROUP BY id HAVING COUNT(*) > 1 LIMIT 5
        """
    )
    dups = cur.fetchall()
    if dups:
        raise RuntimeError(
            "news.id 存在重复值，无法添加主键/唯一；请先消除重复后再迁移。"
            f" 示例: {dups}"
        )

    _log("[Schema] news 上未找到单列 id 的主键/唯一约束，尝试添加 PRIMARY KEY (id) …")
    try:
        cur.execute("ALTER TABLE news ADD CONSTRAINT news_pkey PRIMARY KEY (id)")
        _log("[Schema] 已添加 news_pkey PRIMARY KEY (id)。")
    except Exception as e:
        msg = str(e).lower()
        if "multiple primary keys" not in msg:
            raise
        cur.execute(
            """
            SELECT 1 FROM pg_constraint c
            JOIN pg_class t ON c.conrelid = t.oid
            JOIN pg_namespace n ON t.relnamespace = n.oid
            WHERE n.nspname = 'public' AND t.relname = 'news'
              AND c.conname = 'news_id_unique_for_fk'
            """
        )
        if cur.fetchone() is None:
            cur.execute(
                "ALTER TABLE news ADD CONSTRAINT news_id_unique_for_fk UNIQUE (id)"
            )
            _log(
                "[Schema] 表上已有其它主键，已为 id 添加 UNIQUE 约束，供 news_analysis 外键使用。"
            )
        else:
            _log("[Schema] news_id_unique_for_fk 已存在，跳过。")


def ensure_news_id_referenced_unique_safe(cur, log: Callable[..., None] | None = None) -> bool:
    """与 ensure_news_id_referenced_unique 相同，但捕获异常并返回 False。"""
    try:
        ensure_news_id_referenced_unique(cur, log=log)
        return True
    except Exception as e:
        (log or print)(f"[Schema] ensure_news_id_referenced_unique 失败: {type(e).__name__}: {e}")
        return False
