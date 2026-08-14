import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from hashlib import md5, sha256
from time import perf_counter
from typing import Any, Dict, List, Set, Sequence, Tuple
from uuid import UUID, uuid4

# pymilvus reads MILVUS_URI at import-time. We use GLOBEMIND_MILVUS_URI only.
os.environ.pop("MILVUS_URI", None)
from pymilvus import DataType, MilvusClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config.settings import get_settings
from core_pipeline.ingest_preflight import IngressPreflight
from core_pipeline.simhash import hamming_distance_signed
from core_pipeline.source_tier import DomainTierRule, SourceTierResolver, default_seed_rules


logger = logging.getLogger("db_utils")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | db_utils | %(message)s",
    )


class PostgresManager:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.engine: Engine = create_engine(
            self.settings.pg_dsn,
            pool_pre_ping=True,
            pool_size=self.settings.sqlalchemy_pool_size,
            max_overflow=self.settings.sqlalchemy_max_overflow,
            pool_timeout=self.settings.sqlalchemy_pool_timeout,
            pool_recycle=self.settings.sqlalchemy_pool_recycle,
            future=True,
        )
        self._pg_total = 0
        self._pg_elapsed = 0.0

    def upsert_ai_analysis(
        self,
        news_id: Any = None,
        china_index: float | None = None,
        cluster_id: str | None = None,
        records: Sequence[Dict[str, Any]] | None = None,
        batch_size: int = 1000,
    ) -> int:
        if records is None:
            if news_id is None:
                logger.info("upsert_ai_analysis skipped: empty input")
                return 0
            records = [
                {
                    "news_id": news_id,
                    "china_index": china_index,
                    "cluster_id": cluster_id,
                }
            ]

        if not records:
            logger.info("upsert_ai_analysis skipped: empty records")
            return 0

        sql = text(
            """
            INSERT INTO news_ai_analysis (
                news_id, china_index, cluster_id, analyzed_at
            ) VALUES (
                :news_id, :china_index, :cluster_id, NOW()
            )
            ON CONFLICT (news_id) DO UPDATE SET
                china_index = EXCLUDED.china_index,
                cluster_id = EXCLUDED.cluster_id,
                analyzed_at = NOW()
            """
        )

        total_written = 0
        chunk: List[Dict[str, Any]] = []
        for item in records:
            chunk.append(
                {
                    "news_id": item.get("news_id"),
                    "china_index": item.get("china_index"),
                    "cluster_id": item.get("cluster_id"),
                }
            )
            if len(chunk) >= batch_size:
                total_written += self._write_chunk(sql, chunk)
                chunk.clear()
        if chunk:
            total_written += self._write_chunk(sql, chunk)
        return total_written

    def _write_chunk(self, sql_stmt: Any, chunk: Sequence[Dict[str, Any]]) -> int:
        started = perf_counter()
        written = 0
        try:
            with self.engine.begin() as conn:
                conn.execute(sql_stmt, list(chunk))
                written = len(chunk)
        except Exception:
            logger.exception("batch upsert failed, fallback to row-level upsert")
            for row in chunk:
                try:
                    with self.engine.begin() as conn:
                        conn.execute(sql_stmt, [row])
                        written += 1
                except Exception:
                    logger.exception("single-row upsert failed for news_id=%s", row.get("news_id"))

        elapsed = perf_counter() - started
        self._pg_total += written
        self._pg_elapsed += elapsed
        if self._pg_total and self._pg_total % 1000 == 0:
            avg = self._pg_elapsed / max(self._pg_total / 1000, 1)
            logger.info(
                "pg upsert batch progress=%d avg_latency_per_1k=%.4fs",
                self._pg_total,
                avg,
            )
        else:
            logger.info("pg upsert chunk rows=%d elapsed=%.4fs", written, elapsed)
        return written

    def near_duplicate_scan(
        self,
        signed_simhash: int,
        *,
        exclude_news_id: Any | None,
        window_minutes: int,
        hamming_max: int,
        scan_limit: int,
    ) -> tuple[int, str | None, Any | None]:
        """Count SimHash neighbors in PG window; return anchor fingerprint_family_id + canonical news id."""
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        if "news_ingest_meta" not in set(insp.get_table_names()):
            return 0, None, None
        sql = text(
            """
            SELECT news_id, content_simhash, fingerprint_family_id::text
            FROM news_ingest_meta
            WHERE updated_at >= NOW() - (:window_minutes * INTERVAL '1 minute')
              AND content_simhash IS NOT NULL
            ORDER BY updated_at DESC
            LIMIT :scan_limit
            """
        )
        rows: list[tuple[Any, Any, Any]] = []
        try:
            with self.engine.connect() as conn:
                rows = list(
                    conn.execute(
                        sql,
                        {"window_minutes": window_minutes, "scan_limit": scan_limit},
                    )
                )
        except Exception:
            logger.exception("near_duplicate_scan query failed")
            return 0, None, None

        count = 0
        anchor_family: str | None = None
        canonical_id: Any | None = None
        for news_id, other_signed, fam in rows:
            if exclude_news_id is not None and news_id == exclude_news_id:
                continue
            try:
                dist = hamming_distance_signed(int(signed_simhash), int(other_signed))
            except Exception:
                continue
            if dist <= hamming_max:
                count += 1
                if anchor_family is None and fam:
                    anchor_family = str(fam)
                    canonical_id = news_id
        return count, anchor_family, canonical_id

    def fetch_domain_tier_rules(self) -> List[DomainTierRule]:
        sql = text(
            """
            SELECT domain_pattern, tier, priority
            FROM source_domain_tier
            WHERE active = TRUE
            ORDER BY priority DESC
            """
        )
        rows: List[DomainTierRule] = []
        try:
            with self.engine.connect() as conn:
                for pattern, tier, priority in conn.execute(sql):
                    rows.append(
                        DomainTierRule(
                            domain_pattern=str(pattern),
                            tier=int(tier),
                            priority=int(priority),
                        )
                    )
        except Exception:
            logger.exception("fetch_domain_tier_rules failed; caller may fall back to seeds")
        return rows

    def build_ingest_preflight(self) -> IngressPreflight:
        resolver = SourceTierResolver(default_seed_rules())
        db_rules = self.fetch_domain_tier_rules()
        if db_rules:
            resolver.add_rules(db_rules)
        return IngressPreflight(resolver=resolver)

    def insert_analysis_lineage(
        self,
        *,
        run_id: UUID | None = None,
        pipeline_version: str,
        model_manifest: Dict[str, Any] | None = None,
        input_content_hash: str | None = None,
        constraint_snapshot_id: str | None = None,
        output_entity_id: str | None = None,
        operator: str = "system",
        meta: Dict[str, Any] | None = None,
    ) -> UUID:
        rid = run_id or uuid4()
        sql = text(
            """
            INSERT INTO analysis_lineage (
              run_id, pipeline_version, model_manifest, input_content_hash,
              constraint_snapshot_id, output_entity_id, operator, meta
            ) VALUES (
              :run_id, :pipeline_version, CAST(:model_manifest AS JSONB), :input_content_hash,
              :constraint_snapshot_id, :output_entity_id, :operator, CAST(:meta AS JSONB)
            )
            """
        )
        payload = {
            "run_id": rid,
            "pipeline_version": pipeline_version,
            "model_manifest": json.dumps(model_manifest) if model_manifest is not None else None,
            "input_content_hash": input_content_hash,
            "constraint_snapshot_id": constraint_snapshot_id,
            "output_entity_id": output_entity_id,
            "operator": operator,
            "meta": json.dumps(meta) if meta is not None else None,
        }
        with self.engine.begin() as conn:
            conn.execute(sql, payload)
        return rid

    def patch_analysis_lineage_meta(self, run_id: UUID, patch: Dict[str, Any]) -> None:
        """Merge JSONB patch into ``analysis_lineage.meta`` (e.g. fast_track_event_id back-link)."""
        sql = text(
            """
            UPDATE analysis_lineage
            SET meta = COALESCE(meta, '{}'::jsonb) || CAST(:patch AS JSONB)
            WHERE run_id = :run_id
            """
        )
        with self.engine.begin() as conn:
            conn.execute(
                sql,
                {"run_id": run_id, "patch": json.dumps(patch)},
            )

    @staticmethod
    def coerce_news_pk(raw: Any) -> Any:
        if raw is None:
            return None
        if isinstance(raw, str):
            s = raw.strip()
            if s.isdigit():
                return int(s)
            try:
                return int(s)
            except ValueError:
                return raw
        return raw

    def fetch_news_ai_analysis_row(self, news_id: Any) -> Dict[str, Any] | None:
        nid = self.coerce_news_pk(news_id)
        sql = text(
            """
            SELECT china_index, cluster_id
            FROM news_ai_analysis WHERE news_id = :nid LIMIT 1
            """
        )
        try:
            with self.engine.connect() as conn:
                row = conn.execute(sql, {"nid": nid}).mappings().first()
                return dict(row) if row else None
        except Exception:
            logger.exception("fetch_news_ai_analysis_row failed news_id=%s", news_id)
            return None

    def apply_shadow_inheritance_from_canonical(
        self,
        shadow_news_id: Any,
        canonical_news_id: Any,
    ) -> Dict[str, Any]:
        """Copy enrich fields from canonical article to near-duplicate shadow (Phase 2 / 涉华指数)."""
        canon = self.fetch_news_ai_analysis_row(canonical_news_id)
        if not canon:
            return {"ok": False, "reason": "canonical_analysis_missing"}
        settings = get_settings()
        ci = canon.get("china_index")
        sid = self.coerce_news_pk(shadow_news_id)
        exist = self.fetch_news_ai_analysis_row(sid)
        if ci is None and exist:
            ci = exist.get("china_index")
        if settings.shadow_inherit_cluster_id:
            cl = canon.get("cluster_id")
        else:
            cl = exist.get("cluster_id") if exist else None
        if ci is None and cl is None:
            return {"ok": False, "reason": "canonical_enrich_empty"}
        self.upsert_ai_analysis(news_id=sid, china_index=ci, cluster_id=cl)
        return {"ok": True, "china_index": ci, "cluster_id": cl}

    def enqueue_slow_track_handoff(
        self,
        *,
        news_id: Any,
        fast_track_event_id: int | None = None,
        lineage_run_id: UUID | None = None,
        priority: int | None = None,
    ) -> int:
        """PG-backed slow-track queue row (physical split vs in-process fast path)."""
        settings = get_settings()
        pri = int(priority if priority is not None else settings.slow_track_handoff_priority_boost)
        sql = text(
            """
            INSERT INTO slow_track_handoff (
              news_id, fast_track_event_id, lineage_run_id, priority, status
            ) VALUES (
              :news_id, :ft_eid, :lineage_rid, :priority, 'pending'
            )
            RETURNING id
            """
        )
        with self.engine.begin() as conn:
            row = conn.execute(
                sql,
                {
                    "news_id": self.coerce_news_pk(news_id),
                    "ft_eid": fast_track_event_id,
                    "lineage_rid": lineage_run_id,
                    "priority": pri,
                },
            ).fetchone()
        return int(row[0]) if row else -1

    def fetch_pending_slow_track_handoffs(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        sql = text(
            """
            SELECT id, news_id, fast_track_event_id, lineage_run_id, priority, status, created_at
            FROM slow_track_handoff
            WHERE status = 'pending'
            ORDER BY priority DESC, created_at ASC
            LIMIT :lim
            """
        )
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(sql, {"lim": max(1, int(limit))}).mappings().all()
                return [dict(r) for r in rows]
        except Exception:
            logger.exception("fetch_pending_slow_track_handoffs failed")
            return []

    def fetch_slow_track_handoffs_by_status(self, *, statuses: Sequence[str], limit: int = 50) -> List[Dict[str, Any]]:
        sts = [str(x).strip() for x in statuses if str(x).strip()]
        if not sts:
            return []
        ph = ",".join(f":s{i}" for i in range(len(sts)))
        params = {f"s{i}": sts[i] for i in range(len(sts))}
        params["lim"] = max(1, int(limit))
        sql = text(
            f"""
            SELECT id, news_id, fast_track_event_id, lineage_run_id, priority, status, created_at
            FROM slow_track_handoff
            WHERE status IN ({ph})
            ORDER BY priority DESC, created_at ASC
            LIMIT :lim
            """
        )
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(sql, params).mappings().all()
                return [dict(r) for r in rows]
        except Exception:
            logger.exception("fetch_slow_track_handoffs_by_status failed statuses=%s", sts)
            return []

    def complete_slow_track_handoff(self, handoff_id: int, *, status: str = "done") -> None:
        sql = text(
            """
            UPDATE slow_track_handoff
            SET status = :st
            WHERE id = :id
            """
        )
        with self.engine.begin() as conn:
            conn.execute(sql, {"id": handoff_id, "st": status})

    def count_slow_track_handoff_pending(self) -> int:
        sql = text("SELECT COUNT(*) FROM slow_track_handoff WHERE status = 'pending'")
        try:
            with self.engine.connect() as conn:
                row = conn.execute(sql).fetchone()
                return int(row[0]) if row else 0
        except Exception:
            logger.exception("count_slow_track_handoff_pending failed")
            return -1

    def count_slow_track_handoffs_by_status(self, *, statuses: Sequence[str]) -> int:
        sts = [str(x).strip() for x in statuses if str(x).strip()]
        if not sts:
            return 0
        ph = ",".join(f":s{i}" for i in range(len(sts)))
        params = {f"s{i}": sts[i] for i in range(len(sts))}
        sql = text(f"SELECT COUNT(*) FROM slow_track_handoff WHERE status IN ({ph})")
        try:
            with self.engine.connect() as conn:
                row = conn.execute(sql, params).fetchone()
                return int(row[0]) if row else 0
        except Exception:
            logger.exception("count_slow_track_handoffs_by_status failed statuses=%s", sts)
            return -1

    def fetch_evaluation_gold_rows(self, *, limit: int = 500) -> List[Dict[str, Any]]:
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        if "evaluation_gold_labels" not in set(insp.get_table_names()):
            return []
        sql = text(
            """
            SELECT id, event_key, micro_cluster_ids, notes, created_at
            FROM evaluation_gold_labels
            ORDER BY id ASC
            LIMIT :lim
            """
        )
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(sql, {"lim": max(1, int(limit))}).mappings().all()
                return [dict(r) for r in rows]
        except Exception:
            logger.exception("fetch_evaluation_gold_rows failed")
            return []

    def fetch_micro_cluster_language_map(self, cluster_ids: Sequence[str]) -> Dict[str, str]:
        """Best-effort dominant language per micro cluster from ``news`` rows linked by ``news_ai_analysis``."""
        if not cluster_ids:
            return {}
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        names = set(insp.get_table_names())
        if "news" not in names or "news_ai_analysis" not in names:
            return {}
        cols = {c["name"] for c in insp.get_columns("news")}
        lang_col = None
        for cand in ("language", "lang", "source_language"):
            if cand in cols:
                lang_col = cand
                break
        if not lang_col:
            return {}

        ids = [str(x) for x in cluster_ids if x]
        if not ids:
            return {}
        out: Dict[str, str] = {}
        chunk = 200
        try:
            with self.engine.connect() as conn:
                for i in range(0, len(ids), chunk):
                    part = ids[i : i + chunk]
                    ph = ",".join(f":p{j}" for j in range(len(part)))
                    params = {f"p{j}": part[j] for j in range(len(part))}
                    sql = text(
                        f"""
                        SELECT na.cluster_id::text AS cid, lower(trim(CAST(n.{lang_col} AS TEXT))) AS lang, COUNT(*) AS c
                        FROM news_ai_analysis na
                        INNER JOIN news n ON n.id = na.news_id
                        WHERE na.cluster_id::text IN ({ph})
                          AND n.{lang_col} IS NOT NULL
                          AND trim(CAST(n.{lang_col} AS TEXT)) <> ''
                        GROUP BY na.cluster_id::text, lower(trim(CAST(n.{lang_col} AS TEXT)))
                        """
                    )
                    rows = conn.execute(sql, params).fetchall()
                    # Keep the most frequent language per cluster for stability.
                    tmp: Dict[str, tuple[str, int]] = {}
                    for cid, lang, c in rows:
                        cid_s = str(cid)
                        cur = tmp.get(cid_s)
                        cnt = int(c or 0)
                        if cur is None or cnt > cur[1]:
                            tmp[cid_s] = (str(lang), cnt)
                    for cid_s, (lang_s, _c) in tmp.items():
                        out[cid_s] = lang_s
        except Exception:
            logger.exception("fetch_micro_cluster_language_map failed")
            return {}
        return out

    def fetch_micro_cluster_last_article_map(self, cluster_ids: Sequence[str]) -> Dict[str, Any]:
        if not cluster_ids:
            return {}
        ids = [str(x) for x in cluster_ids if x]
        if not ids:
            return {}
        out: Dict[str, Any] = {}
        chunk = 200
        try:
            with self.engine.connect() as conn:
                for i in range(0, len(ids), chunk):
                    part = ids[i : i + chunk]
                    ph = ",".join(f":p{j}" for j in range(len(part)))
                    params = {f"p{j}": part[j] for j in range(len(part))}
                    sql = text(
                        f"""
                        SELECT cluster_id::text AS cid, last_article_at
                        FROM micro_cluster_registry
                        WHERE cluster_id::text IN ({ph})
                        """
                    )
                    for cid, ts in conn.execute(sql, params):
                        out[str(cid)] = ts
        except Exception:
            logger.exception("fetch_micro_cluster_last_article_map failed")
            return {}
        return out

    def fetch_micro_cluster_source_tier_map(self, cluster_ids: Sequence[str]) -> Dict[str, int]:
        """Dominant source_tier per micro cluster from ``news_ingest_meta`` linked by ``news_ai_analysis``."""
        if not cluster_ids:
            return {}
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        if "news_ingest_meta" not in set(insp.get_table_names()):
            return {}
        ids = [str(x) for x in cluster_ids if x]
        if not ids:
            return {}
        out: Dict[str, int] = {}
        chunk = 200
        try:
            with self.engine.connect() as conn:
                for i in range(0, len(ids), chunk):
                    part = ids[i : i + chunk]
                    ph = ",".join(f":p{j}" for j in range(len(part)))
                    params = {f"p{j}": part[j] for j in range(len(part))}
                    sql = text(
                        f"""
                        SELECT na.cluster_id::text AS cid, nim.source_tier, COUNT(*) AS c
                        FROM news_ai_analysis na
                        INNER JOIN news_ingest_meta nim ON nim.news_id = na.news_id
                        WHERE na.cluster_id::text IN ({ph})
                          AND nim.source_tier IS NOT NULL
                        GROUP BY na.cluster_id::text, nim.source_tier
                        """
                    )
                    rows = conn.execute(sql, params).fetchall()
                    tmp: Dict[str, tuple[int, int]] = {}
                    for cid, tier, c in rows:
                        cid_s = str(cid)
                        cnt = int(c or 0)
                        t = int(tier)
                        cur = tmp.get(cid_s)
                        if cur is None or cnt > cur[1]:
                            tmp[cid_s] = (t, cnt)
                    for cid_s, (tier, _cnt) in tmp.items():
                        out[cid_s] = tier
        except Exception:
            logger.exception("fetch_micro_cluster_source_tier_map failed")
            return {}
        return out

    def upsert_evaluation_gold_label(
        self,
        *,
        event_key: str,
        micro_cluster_ids: Sequence[str],
        notes: str | None = None,
    ) -> None:
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        if "evaluation_gold_labels" not in set(insp.get_table_names()):
            logger.info("upsert_evaluation_gold_label skipped: table missing")
            return
        sql = text(
            """
            INSERT INTO evaluation_gold_labels (event_key, micro_cluster_ids, notes)
            VALUES (:ek, CAST(:mc AS JSONB), :notes)
            ON CONFLICT (event_key) DO UPDATE SET
              micro_cluster_ids = EXCLUDED.micro_cluster_ids,
              notes = EXCLUDED.notes
            """
        )
        payload = {
            "ek": str(event_key).strip(),
            "mc": json.dumps([str(x) for x in micro_cluster_ids if x is not None]),
            "notes": notes,
        }
        with self.engine.begin() as conn:
            conn.execute(sql, payload)

    def fetch_news_row_for_embedding(self, news_id: Any) -> Dict[str, Any] | None:
        """Single ``news`` row shaped like ``news_cursor`` output (title/summary/published_at)."""
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        names = set(insp.get_table_names())
        if "news" not in names:
            return None
        col_names = {c["name"] for c in insp.get_columns("news")}
        if "id" not in col_names:
            return None
        fields = ["n.id AS id"]
        if "title" in col_names:
            fields.append("n.title AS title")
        else:
            fields.append("CAST(NULL AS TEXT) AS title")
        if "summary" in col_names:
            fields.append("n.summary AS summary")
        elif "content" in col_names:
            fields.append("LEFT(n.content, 2000) AS summary")
        else:
            fields.append("CAST(NULL AS TEXT) AS summary")
        if "published_at" in col_names:
            fields.append("n.published_at AS published_at")
        else:
            fields.append("CAST(NULL AS TIMESTAMPTZ) AS published_at")
        sql = text(f"SELECT {', '.join(fields)} FROM news AS n WHERE n.id = :nid LIMIT 1")
        nid = self.coerce_news_pk(news_id)
        try:
            with self.engine.connect() as conn:
                row = conn.execute(sql, {"nid": nid}).mappings().first()
                return dict(row) if row else None
        except Exception:
            logger.exception("fetch_news_row_for_embedding failed news_id=%s", news_id)
            return None

    def fetch_news_row_for_slow_track_enrich(self, news_id: Any) -> Dict[str, Any] | None:
        """Like ``fetch_news_row_for_embedding`` plus optional ``body_text`` from ``content`` for NER/translate."""
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        names = set(insp.get_table_names())
        if "news" not in names:
            return None
        col_names = {c["name"] for c in insp.get_columns("news")}
        if "id" not in col_names:
            return None
        fields = ["n.id AS id"]
        if "title" in col_names:
            fields.append("n.title AS title")
        else:
            fields.append("CAST(NULL AS TEXT) AS title")
        if "summary" in col_names:
            fields.append("n.summary AS summary")
        elif "content" in col_names:
            fields.append("LEFT(n.content, 2000) AS summary")
        else:
            fields.append("CAST(NULL AS TEXT) AS summary")
        if "content" in col_names:
            fields.append("LEFT(n.content, 40000) AS body_text")
        else:
            fields.append("CAST(NULL AS TEXT) AS body_text")
        if "published_at" in col_names:
            fields.append("n.published_at AS published_at")
        else:
            fields.append("CAST(NULL AS TIMESTAMPTZ) AS published_at")
        sql = text(f"SELECT {', '.join(fields)} FROM news AS n WHERE n.id = :nid LIMIT 1")
        nid = self.coerce_news_pk(news_id)
        try:
            with self.engine.connect() as conn:
                row = conn.execute(sql, {"nid": nid}).mappings().first()
                return dict(row) if row else None
        except Exception:
            logger.exception("fetch_news_row_for_slow_track_enrich failed news_id=%s", news_id)
            return None

    def upsert_news_enrichment_atomic(
        self,
        *,
        news_id: Any,
        translated_title: str | None = None,
        translated_body: str | None = None,
        entities: List[Dict[str, Any]],
    ) -> None:
        """Single transaction: persist translations + entity JSON snapshots."""
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        names = set(insp.get_table_names())
        has_news_translation = "news_translation" in names
        has_analysis = "news_ai_analysis" in names

        nid = self.coerce_news_pk(news_id)
        entities_json = json.dumps(entities or [], ensure_ascii=False)
        # Always overwrite title/body when this upsert runs with provided strings (use "" not NULL).
        # COALESCE(EXCLUDED, old) would keep NULL from skipping title when callers only passed body.
        upsert_translation = text(
            """
            INSERT INTO news_translation (news_id, title, body, updated_at)
            VALUES (:nid, :tt, :tb, NOW())
            ON CONFLICT (news_id) DO UPDATE
              SET title = EXCLUDED.title,
                  body = EXCLUDED.body,
                  updated_at = NOW()
            """
        )
        upsert_entities = text(
            """
            INSERT INTO news_ai_analysis (news_id, entities, analyzed_at)
            VALUES (:nid, CAST(:ej AS JSONB), NOW())
            ON CONFLICT (news_id) DO UPDATE
              SET entities = EXCLUDED.entities,
                  analyzed_at = NOW()
            """
        )

        with self.engine.begin() as conn:
            if has_news_translation and (translated_title is not None or translated_body is not None):
                conn.execute(
                    upsert_translation,
                    {
                        "nid": nid,
                        "tt": translated_title if translated_title is not None else "",
                        "tb": translated_body if translated_body is not None else "",
                    },
                )
            if has_analysis:
                conn.execute(upsert_entities, {"nid": nid, "ej": entities_json})

    def persist_llm_enrich_atomic(
        self,
        *,
        news_id: Any,
        translated_title: str,
        translated_body: str,
        translation_quality: str = "full",
        analysis: Dict[str, Any],
        entities: Sequence[Dict[str, Any]],
    ) -> None:
        """
        Single transaction end-to-end persist to avoid half-written rows:
        - news_translation(title/body)
        - news_ai_analysis(analysis fields + entities JSONB)
        """
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        names = set(insp.get_table_names())
        if "news_translation" not in names:
            raise RuntimeError("table missing: news_translation")
        if "news_ai_analysis" not in names:
            raise RuntimeError("table missing: news_ai_analysis")

        na_cols = {c["name"] for c in insp.get_columns("news_ai_analysis")}
        if "china_relevance_score" not in na_cols:
            raise RuntimeError(
                "news_ai_analysis missing required column china_relevance_score; run migration scripts first"
            )
        has_impact = "china_impact_sentiment" in na_cols
        has_evidence = "scoring_evidence" in na_cols
        has_exact_quotes = "exact_quotes" in na_cols
        has_sub_tags = "sub_tags" in na_cols
        nt_cols = {c["name"] for c in insp.get_columns("news_translation")}
        has_translation_quality = "translation_quality" in nt_cols

        nid = self.coerce_news_pk(news_id)
        tt = (translated_title or "").strip()
        tb = (translated_body or "").strip()

        # Normalize analysis payloads.
        score = int(analysis.get("china_relevance_score") or 0)
        score = max(0, min(10, score))
        is_china_related = bool(analysis.get("is_china_related")) if "is_china_related" in analysis else (score >= 1)
        category = str(analysis.get("category") or "")
        topic = str(analysis.get("topic") or "")
        impact_level = int(analysis.get("impact_level") or 1)
        impact_level = max(1, min(5, impact_level))
        raw_ev = analysis.get("scoring_evidence")
        if raw_ev is None:
            evidence_val = None
        else:
            evidence_val = str(raw_ev)[:2048]
        raw_eq = analysis.get("exact_quotes")
        if raw_eq is None:
            exact_quotes_val = None
        else:
            exact_quotes_val = str(raw_eq).strip()[:8000] or None
        impact = float(analysis.get("china_impact_sentiment") or 0.0) if has_impact else None
        evidence = evidence_val if has_evidence else None
        sub_tags_json = (
            json.dumps([str(x) for x in (analysis.get("sub_tags") or []) if str(x).strip()]) if has_sub_tags else None
        )

        entities_list = list(entities or [])
        entities_json = json.dumps(entities_list, ensure_ascii=False)

        has_cn_kw = "china_related_keywords" in na_cols
        cn_kw_raw = analysis.get("china_related_keywords")
        cn_kw_json: str | None
        if isinstance(cn_kw_raw, list):
            cn_kw_json = json.dumps([str(x) for x in cn_kw_raw if str(x).strip()], ensure_ascii=False)
        elif cn_kw_raw is None:
            cn_kw_json = None
        else:
            cn_kw_json = json.dumps([str(cn_kw_raw)], ensure_ascii=False)

        # Non–China: omit 涉华-only fields (sentiment / evidence / china keywords). Still persist
        # entities + sub_tags — the LLM enricher fills these on fast path (classification, geo supplement)
        # and downstream (e.g. gravity / listings) expects them even when is_china_related is false.
        if not is_china_related:
            evidence = None if has_evidence else None
            impact = None if has_impact else None
            cn_kw_json = None if has_cn_kw else None
            exact_quotes_val = None if has_exact_quotes else None

        if has_translation_quality:
            upsert_translation = text(
                """
                INSERT INTO news_translation (news_id, title, body, translation_quality, updated_at)
                VALUES (:nid, :tt, :tb, :tq, NOW())
                ON CONFLICT (news_id) DO UPDATE
                  SET title = EXCLUDED.title,
                      body = EXCLUDED.body,
                      translation_quality = EXCLUDED.translation_quality,
                      updated_at = NOW()
                """
            )
        else:
            upsert_translation = text(
                """
                INSERT INTO news_translation (news_id, title, body, updated_at)
                VALUES (:nid, :tt, :tb, NOW())
                ON CONFLICT (news_id) DO UPDATE
                  SET title = EXCLUDED.title,
                      body = EXCLUDED.body,
                      updated_at = NOW()
                """
            )

        # Build analysis upsert dynamically for optional columns.
        insert_cols = ["news_id", "china_relevance_score", "is_china_related", "category", "topic", "impact_level", "entities", "analyzed_at"]
        values = [":news_id", ":china_relevance_score", ":is_china_related", ":category", ":topic", ":impact_level", "CAST(:entities AS JSONB)", "NOW()"]
        updates = [
            "china_relevance_score = COALESCE(EXCLUDED.china_relevance_score, news_ai_analysis.china_relevance_score)",
            "is_china_related = COALESCE(EXCLUDED.is_china_related, news_ai_analysis.is_china_related)",
            "category = COALESCE(EXCLUDED.category, news_ai_analysis.category)",
            "topic = COALESCE(EXCLUDED.topic, news_ai_analysis.topic)",
            "impact_level = COALESCE(EXCLUDED.impact_level, news_ai_analysis.impact_level)",
            "entities = COALESCE(EXCLUDED.entities, news_ai_analysis.entities)",
            "analyzed_at = NOW()",
        ]
        if has_impact:
            insert_cols.insert(2, "china_impact_sentiment")
            values.insert(2, ":china_impact_sentiment")
            updates.insert(1, "china_impact_sentiment = COALESCE(EXCLUDED.china_impact_sentiment, news_ai_analysis.china_impact_sentiment)")
        if has_evidence:
            insert_cols.insert(3, "scoring_evidence")
            values.insert(3, ":scoring_evidence")
            updates.insert(2, "scoring_evidence = COALESCE(EXCLUDED.scoring_evidence, news_ai_analysis.scoring_evidence)")
        if has_exact_quotes:
            insert_cols.insert(4, "exact_quotes")
            values.insert(4, ":exact_quotes")
            updates.insert(3, "exact_quotes = COALESCE(EXCLUDED.exact_quotes, news_ai_analysis.exact_quotes)")
        if has_sub_tags:
            insert_cols.insert(5 if has_exact_quotes else 4, "sub_tags")
            values.insert(5 if has_exact_quotes else 4, "CAST(:sub_tags AS JSONB)")
            updates.insert(4 if has_exact_quotes else 3, "sub_tags = COALESCE(EXCLUDED.sub_tags, news_ai_analysis.sub_tags)")
        if has_cn_kw:
            ie = insert_cols.index("entities")
            insert_cols.insert(ie, "china_related_keywords")
            values.insert(ie, "CAST(:china_related_keywords AS JSONB)")
            updates.insert(-2, "china_related_keywords = COALESCE(EXCLUDED.china_related_keywords, news_ai_analysis.china_related_keywords)")

        upsert_analysis = text(
            f"""
            INSERT INTO news_ai_analysis ({', '.join(insert_cols)})
            VALUES ({', '.join(values)})
            ON CONFLICT (news_id) DO UPDATE SET
              {', '.join(updates)}
            """
        )

        with self.engine.begin() as conn:
            trans_params = {"nid": nid, "tt": tt, "tb": tb}
            if has_translation_quality:
                trans_params["tq"] = str(translation_quality or "full")[:20]
            conn.execute(upsert_translation, trans_params)
            upsert_params: Dict[str, Any] = {
                "news_id": nid,
                "china_relevance_score": score,
                "china_impact_sentiment": impact,
                "scoring_evidence": evidence,
                "exact_quotes": exact_quotes_val,
                "sub_tags": sub_tags_json,
                "is_china_related": is_china_related,
                "category": category,
                "topic": topic,
                "impact_level": impact_level,
                "entities": entities_json,
            }
            if has_cn_kw:
                upsert_params["china_related_keywords"] = cn_kw_json
            conn.execute(upsert_analysis, upsert_params)

    def upsert_entity_pair_sentiments_only(
        self,
        *,
        news_id: Any,
        pairs: Sequence[Dict[str, Any]],
    ) -> None:
        """
        Set ``news_ai_analysis.entity_pair_sentiments`` without touching translation or other analysis columns.
        """
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        if "news_ai_analysis" not in set(insp.get_table_names()):
            raise RuntimeError("table missing: news_ai_analysis")
        cols = {c["name"] for c in insp.get_columns("news_ai_analysis")}
        if "entity_pair_sentiments" not in cols:
            raise RuntimeError("news_ai_analysis missing entity_pair_sentiments; run init_db / migrate")
        nid = self.coerce_news_pk(news_id)
        blob = json.dumps(list(pairs or []), ensure_ascii=False)
        sql = text(
            """
            UPDATE news_ai_analysis
            SET entity_pair_sentiments = CAST(:j AS jsonb), analyzed_at = NOW()
            WHERE news_id = :nid
            """
        )
        with self.engine.begin() as conn:
            r = conn.execute(sql, {"nid": nid, "j": blob})
            if (r.rowcount or 0) < 1:
                raise RuntimeError(f"no news_ai_analysis row for news_id={news_id}")

    def persist_llm_enrich_merge(
        self,
        *,
        news_id: Any,
        translated_title: str,
        translated_body: str,
        analysis: Dict[str, Any],
        entities: Sequence[Dict[str, Any]],
    ) -> None:
        """
        Like persist_llm_enrich_atomic, but **never overwrites** non-empty existing data.
        Intended for backfill jobs.
        """
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        names = set(insp.get_table_names())
        if "news_translation" not in names or "news_ai_analysis" not in names:
            raise RuntimeError("required tables missing: news_translation/news_ai_analysis")

        na_cols = {c["name"] for c in insp.get_columns("news_ai_analysis")}
        if "china_relevance_score" not in na_cols:
            raise RuntimeError("news_ai_analysis missing china_relevance_score; run migrations first")

        has_impact = "china_impact_sentiment" in na_cols
        has_evidence = "scoring_evidence" in na_cols
        has_sub_tags = "sub_tags" in na_cols
        has_entities = "entities" in na_cols

        nid = self.coerce_news_pk(news_id)
        tt = (translated_title or "").strip()
        tb = (translated_body or "").strip()

        score = int(analysis.get("china_relevance_score") or 0)
        score = max(0, min(10, score))
        is_china_related = bool(analysis.get("is_china_related")) if "is_china_related" in analysis else (score >= 1)
        category = str(analysis.get("category") or "")
        topic = str(analysis.get("topic") or "")
        impact_level = int(analysis.get("impact_level") or 1)
        impact_level = max(1, min(5, impact_level))
        impact = float(analysis.get("china_impact_sentiment") or 0.0) if has_impact else None
        evidence = (str(analysis.get("scoring_evidence") or "")[:2048] if has_evidence else None)
        sub_tags_json = (
            json.dumps([str(x) for x in (analysis.get("sub_tags") or []) if str(x).strip()]) if has_sub_tags else None
        )

        entities_list = list(entities or [])
        entities_json = json.dumps(entities_list, ensure_ascii=False)

        # Translation: only fill if existing is NULL/empty.
        upsert_translation = text(
            """
            INSERT INTO news_translation (news_id, title, body, updated_at)
            VALUES (:nid, :tt, :tb, NOW())
            ON CONFLICT (news_id) DO UPDATE
              SET title = CASE
                    WHEN btrim(COALESCE(news_translation.title,'')) = '' THEN EXCLUDED.title
                    ELSE news_translation.title
                  END,
                  body = CASE
                    WHEN btrim(COALESCE(news_translation.body,'')) = '' THEN EXCLUDED.body
                    ELSE news_translation.body
                  END,
                  updated_at = NOW()
            """
        )

        # Analysis: fill only when current column is NULL/empty.
        insert_cols = ["news_id", "china_relevance_score", "is_china_related", "category", "topic", "impact_level", "analyzed_at"]
        values = [":news_id", ":china_relevance_score", ":is_china_related", ":category", ":topic", ":impact_level", "NOW()"]
        updates = [
            "china_relevance_score = COALESCE(news_ai_analysis.china_relevance_score, EXCLUDED.china_relevance_score)",
            "is_china_related = COALESCE(news_ai_analysis.is_china_related, EXCLUDED.is_china_related)",
            "category = CASE WHEN btrim(COALESCE(news_ai_analysis.category,'')) = '' THEN EXCLUDED.category ELSE news_ai_analysis.category END",
            "topic = CASE WHEN btrim(COALESCE(news_ai_analysis.topic,'')) = '' THEN EXCLUDED.topic ELSE news_ai_analysis.topic END",
            "impact_level = COALESCE(news_ai_analysis.impact_level, EXCLUDED.impact_level)",
            "analyzed_at = NOW()",
        ]
        if has_impact:
            insert_cols.insert(2, "china_impact_sentiment")
            values.insert(2, ":china_impact_sentiment")
            updates.insert(1, "china_impact_sentiment = COALESCE(news_ai_analysis.china_impact_sentiment, EXCLUDED.china_impact_sentiment)")
        if has_evidence:
            insert_cols.insert(3, "scoring_evidence")
            values.insert(3, ":scoring_evidence")
            updates.insert(2, "scoring_evidence = CASE WHEN btrim(COALESCE(news_ai_analysis.scoring_evidence,'')) = '' THEN EXCLUDED.scoring_evidence ELSE news_ai_analysis.scoring_evidence END")
        if has_sub_tags:
            insert_cols.insert(4, "sub_tags")
            values.insert(4, "CAST(:sub_tags AS JSONB)")
            updates.insert(3, "sub_tags = COALESCE(news_ai_analysis.sub_tags, EXCLUDED.sub_tags)")
        if has_entities:
            insert_cols.insert(-1, "entities")
            values.insert(-1, "CAST(:entities AS JSONB)")
            updates.insert(-1, "entities = CASE WHEN news_ai_analysis.entities IS NULL OR news_ai_analysis.entities = '[]'::jsonb THEN EXCLUDED.entities ELSE news_ai_analysis.entities END")

        upsert_analysis = text(
            f"""
            INSERT INTO news_ai_analysis ({', '.join(insert_cols)})
            VALUES ({', '.join(values)})
            ON CONFLICT (news_id) DO UPDATE SET
              {', '.join(updates)}
            """
        )

        with self.engine.begin() as conn:
            conn.execute(upsert_translation, {"nid": nid, "tt": tt, "tb": tb})
            conn.execute(
                upsert_analysis,
                {
                    "news_id": nid,
                    "china_relevance_score": score,
                    "china_impact_sentiment": impact,
                    "scoring_evidence": evidence,
                    "sub_tags": sub_tags_json,
                    "is_china_related": is_china_related,
                    "category": category,
                    "topic": topic,
                    "impact_level": impact_level,
                    "entities": entities_json,
                },
            )

    def persist_llm_enrich_merge_translation_only(
        self,
        *,
        news_id: Any,
        translated_title: str,
        translated_body: str,
    ) -> None:
        """Merge upsert into ``news_translation`` only (backfill phase 1)."""
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        if "news_translation" not in set(insp.get_table_names()):
            raise RuntimeError("table missing: news_translation")

        nid = self.coerce_news_pk(news_id)
        tt = (translated_title or "").strip()
        tb = (translated_body or "").strip()
        upsert_translation = text(
            """
            INSERT INTO news_translation (news_id, title, body, updated_at)
            VALUES (:nid, :tt, :tb, NOW())
            ON CONFLICT (news_id) DO UPDATE
              SET title = CASE
                    WHEN btrim(COALESCE(news_translation.title,'')) = '' THEN EXCLUDED.title
                    ELSE news_translation.title
                  END,
                  body = CASE
                    WHEN btrim(COALESCE(news_translation.body,'')) = '' THEN EXCLUDED.body
                    ELSE news_translation.body
                  END,
                  updated_at = NOW()
            """
        )
        with self.engine.begin() as conn:
            conn.execute(upsert_translation, {"nid": nid, "tt": tt, "tb": tb})

    def persist_llm_enrich_merge_analysis_only(
        self,
        *,
        news_id: Any,
        analysis: Dict[str, Any],
        entities: Sequence[Dict[str, Any]],
    ) -> None:
        """Merge upsert into ``news_ai_analysis`` only (backfill phase 2)."""
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        if "news_ai_analysis" not in set(insp.get_table_names()):
            raise RuntimeError("table missing: news_ai_analysis")

        na_cols = {c["name"] for c in insp.get_columns("news_ai_analysis")}
        if "china_relevance_score" not in na_cols:
            raise RuntimeError("news_ai_analysis missing china_relevance_score; run migrations first")

        has_impact = "china_impact_sentiment" in na_cols
        has_evidence = "scoring_evidence" in na_cols
        has_sub_tags = "sub_tags" in na_cols
        has_entities = "entities" in na_cols

        nid = self.coerce_news_pk(news_id)
        score = int(analysis.get("china_relevance_score") or 0)
        score = max(0, min(10, score))
        is_china_related = bool(analysis.get("is_china_related")) if "is_china_related" in analysis else (score >= 1)
        category = str(analysis.get("category") or "")
        topic = str(analysis.get("topic") or "")
        impact_level = int(analysis.get("impact_level") or 1)
        impact_level = max(1, min(5, impact_level))
        impact = float(analysis.get("china_impact_sentiment") or 0.0) if has_impact else None
        evidence = (str(analysis.get("scoring_evidence") or "")[:2048] if has_evidence else None)
        sub_tags_json = (
            json.dumps([str(x) for x in (analysis.get("sub_tags") or []) if str(x).strip()]) if has_sub_tags else None
        )
        entities_list = list(entities or [])
        entities_json = json.dumps(entities_list, ensure_ascii=False)

        insert_cols = ["news_id", "china_relevance_score", "is_china_related", "category", "topic", "impact_level", "analyzed_at"]
        values = [":news_id", ":china_relevance_score", ":is_china_related", ":category", ":topic", ":impact_level", "NOW()"]
        updates = [
            "china_relevance_score = COALESCE(news_ai_analysis.china_relevance_score, EXCLUDED.china_relevance_score)",
            "is_china_related = COALESCE(news_ai_analysis.is_china_related, EXCLUDED.is_china_related)",
            "category = CASE WHEN btrim(COALESCE(news_ai_analysis.category,'')) = '' THEN EXCLUDED.category ELSE news_ai_analysis.category END",
            "topic = CASE WHEN btrim(COALESCE(news_ai_analysis.topic,'')) = '' THEN EXCLUDED.topic ELSE news_ai_analysis.topic END",
            "impact_level = COALESCE(news_ai_analysis.impact_level, EXCLUDED.impact_level)",
            "analyzed_at = NOW()",
        ]
        if has_impact:
            insert_cols.insert(2, "china_impact_sentiment")
            values.insert(2, ":china_impact_sentiment")
            updates.insert(1, "china_impact_sentiment = COALESCE(news_ai_analysis.china_impact_sentiment, EXCLUDED.china_impact_sentiment)")
        if has_evidence:
            insert_cols.insert(3, "scoring_evidence")
            values.insert(3, ":scoring_evidence")
            updates.insert(2, "scoring_evidence = CASE WHEN btrim(COALESCE(news_ai_analysis.scoring_evidence,'')) = '' THEN EXCLUDED.scoring_evidence ELSE news_ai_analysis.scoring_evidence END")
        if has_sub_tags:
            insert_cols.insert(4, "sub_tags")
            values.insert(4, "CAST(:sub_tags AS JSONB)")
            updates.insert(3, "sub_tags = COALESCE(news_ai_analysis.sub_tags, EXCLUDED.sub_tags)")
        if has_entities:
            insert_cols.insert(-1, "entities")
            values.insert(-1, "CAST(:entities AS JSONB)")
            updates.insert(-1, "entities = CASE WHEN news_ai_analysis.entities IS NULL OR news_ai_analysis.entities = '[]'::jsonb THEN EXCLUDED.entities ELSE news_ai_analysis.entities END")

        upsert_analysis = text(
            f"""
            INSERT INTO news_ai_analysis ({', '.join(insert_cols)})
            VALUES ({', '.join(values)})
            ON CONFLICT (news_id) DO UPDATE SET
              {', '.join(updates)}
            """
        )
        with self.engine.begin() as conn:
            conn.execute(
                upsert_analysis,
                {
                    "news_id": nid,
                    "china_relevance_score": score,
                    "china_impact_sentiment": impact,
                    "scoring_evidence": evidence,
                    "sub_tags": sub_tags_json,
                    "is_china_related": is_china_related,
                    "category": category,
                    "topic": topic,
                    "impact_level": impact_level,
                    "entities": entities_json,
                },
            )

    def upsert_news_ingest_meta(
        self,
        *,
        news_id: Any,
        content_simhash_signed: int | None = None,
        fingerprint_family_id: str | None = None,
        source_tier: int | None = None,
        quarantine_bucket: str | None = None,
        ingest_decision: str = "accept",
        decision_reason: str | None = None,
        ruleset_version: str | None = None,
        canonical_news_id: Any | None = None,
        centroid_policy: str | None = None,
        # LLM enrichment fields (optional)
        is_china_related: bool | None = None,
        china_index: int | None = None,
        category: str | None = None,
        topic: str | None = None,
        sentiment_score: float | None = None,
        sentiment_label: str | None = None,
        impact_level: int | None = None,
    ) -> None:
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        if "news_ingest_meta" not in set(insp.get_table_names()):
            logger.info("upsert_news_ingest_meta skipped: table missing")
            return
        settings = get_settings()
        ver = ruleset_version or settings.ruleset_version
        policy = centroid_policy or "normal"
        sql = text(
            """
            INSERT INTO news_ingest_meta (
              news_id, content_simhash, fingerprint_family_id, source_tier,
              quarantine_bucket, ingest_decision, decision_reason, ruleset_version,
              canonical_news_id, centroid_policy,
              is_china_related, china_index, category, topic,
              sentiment_score, sentiment_label, impact_level,
              updated_at
            ) VALUES (
              :news_id, :content_simhash, :fingerprint_family_id, :source_tier,
              :quarantine_bucket, :ingest_decision, :decision_reason, :ruleset_version,
              :canonical_news_id, :centroid_policy,
              :is_china_related, :china_index, :category, :topic,
              :sentiment_score, :sentiment_label, :impact_level,
              NOW()
            )
            ON CONFLICT (news_id) DO UPDATE SET
              content_simhash = EXCLUDED.content_simhash,
              fingerprint_family_id = EXCLUDED.fingerprint_family_id,
              source_tier = EXCLUDED.source_tier,
              quarantine_bucket = EXCLUDED.quarantine_bucket,
              ingest_decision = EXCLUDED.ingest_decision,
              decision_reason = EXCLUDED.decision_reason,
              ruleset_version = EXCLUDED.ruleset_version,
              canonical_news_id = COALESCE(EXCLUDED.canonical_news_id, news_ingest_meta.canonical_news_id),
              centroid_policy = COALESCE(EXCLUDED.centroid_policy, news_ingest_meta.centroid_policy),
              is_china_related = COALESCE(EXCLUDED.is_china_related, news_ingest_meta.is_china_related),
              china_index = COALESCE(EXCLUDED.china_index, news_ingest_meta.china_index),
              category = COALESCE(EXCLUDED.category, news_ingest_meta.category),
              topic = COALESCE(EXCLUDED.topic, news_ingest_meta.topic),
              sentiment_score = COALESCE(EXCLUDED.sentiment_score, news_ingest_meta.sentiment_score),
              sentiment_label = COALESCE(EXCLUDED.sentiment_label, news_ingest_meta.sentiment_label),
              impact_level = COALESCE(EXCLUDED.impact_level, news_ingest_meta.impact_level),
              updated_at = NOW()
            """
        )
        with self.engine.begin() as conn:
            conn.execute(
                sql,
                {
                    "news_id": news_id,
                    "content_simhash": content_simhash_signed,
                    "fingerprint_family_id": fingerprint_family_id,
                    "source_tier": source_tier,
                    "quarantine_bucket": quarantine_bucket,
                    "ingest_decision": ingest_decision,
                    "decision_reason": decision_reason,
                    "ruleset_version": ver,
                    "canonical_news_id": canonical_news_id,
                    "centroid_policy": policy,
                    "is_china_related": is_china_related,
                    "china_index": int(china_index) if china_index is not None else None,
                    "category": category,
                    "topic": topic,
                    "sentiment_score": float(sentiment_score) if sentiment_score is not None else None,
                    "sentiment_label": sentiment_label,
                    "impact_level": int(impact_level) if impact_level is not None else None,
                },
            )

    def upsert_llm_analysis_to_news_ai_analysis(
        self,
        *,
        news_id: Any,
        china_relevance_score: int | None = None,
        china_impact_sentiment: float | None = None,
        scoring_evidence: str | None = None,
        exact_quotes: str | None = None,
        is_china_related: bool | None,
        category: str | None,
        sub_tags: Sequence[str] | None = None,
        topic: str | None,
        impact_level: int | None,
        entities: Sequence[Dict[str, Any]] | None,
    ) -> None:
        """
        Persist LLM enrichment outputs into `news_ai_analysis`.
        - analysis fields go to dedicated columns (new columns are added by init/migration scripts)
        - entities are stored as JSONB plus a flat-text form for quick grep/export
        """
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        if "news_ai_analysis" not in set(insp.get_table_names()):
            logger.info("upsert_llm_analysis_to_news_ai_analysis skipped: table missing")
            return
        cols = {c["name"] for c in insp.get_columns("news_ai_analysis")}
        nid = self.coerce_news_pk(news_id)
        entities_list = list(entities or [])
        entities_json = json.dumps(entities_list, ensure_ascii=False)
        has_new_score = "china_relevance_score" in cols
        has_impact = "china_impact_sentiment" in cols
        has_evidence = "scoring_evidence" in cols
        has_exact_quotes = "exact_quotes" in cols
        has_sub_tags = "sub_tags" in cols
        if not has_new_score:
            raise RuntimeError(
                "news_ai_analysis missing required column china_relevance_score; run migration scripts first"
            )

        insert_cols = [
            "news_id",
            "china_relevance_score",
            "is_china_related",
            "category",
            "topic",
            "impact_level",
            "entities",
            "analyzed_at",
        ]
        values = [
            ":news_id",
            ":china_relevance_score",
            ":is_china_related",
            ":category",
            ":topic",
            ":impact_level",
            "CAST(:entities AS JSONB)",
            "NOW()",
        ]
        updates = [
            "china_relevance_score = COALESCE(EXCLUDED.china_relevance_score, news_ai_analysis.china_relevance_score)",
            "is_china_related = COALESCE(EXCLUDED.is_china_related, news_ai_analysis.is_china_related)",
            "category = COALESCE(EXCLUDED.category, news_ai_analysis.category)",
            "topic = COALESCE(EXCLUDED.topic, news_ai_analysis.topic)",
            "impact_level = COALESCE(EXCLUDED.impact_level, news_ai_analysis.impact_level)",
            "entities = COALESCE(EXCLUDED.entities, news_ai_analysis.entities)",
            "analyzed_at = NOW()",
        ]
        if has_impact:
            insert_cols.insert(2, "china_impact_sentiment")
            values.insert(2, ":china_impact_sentiment")
            updates.insert(1, "china_impact_sentiment = COALESCE(EXCLUDED.china_impact_sentiment, news_ai_analysis.china_impact_sentiment)")
        if has_evidence:
            insert_cols.insert(3, "scoring_evidence")
            values.insert(3, ":scoring_evidence")
            updates.insert(2, "scoring_evidence = COALESCE(EXCLUDED.scoring_evidence, news_ai_analysis.scoring_evidence)")
        if has_exact_quotes:
            insert_cols.insert(4, "exact_quotes")
            values.insert(4, ":exact_quotes")
            updates.insert(3, "exact_quotes = COALESCE(EXCLUDED.exact_quotes, news_ai_analysis.exact_quotes)")
        if has_sub_tags:
            insert_cols.insert(5 if has_exact_quotes else 4, "sub_tags")
            values.insert(5 if has_exact_quotes else 4, "CAST(:sub_tags AS JSONB)")
            updates.insert(4 if has_exact_quotes else 3, "sub_tags = COALESCE(EXCLUDED.sub_tags, news_ai_analysis.sub_tags)")

        sql = text(
            f"""
            INSERT INTO news_ai_analysis ({', '.join(insert_cols)})
            VALUES ({', '.join(values)})
            ON CONFLICT (news_id) DO UPDATE SET
              {', '.join(updates)}
            """
        )
        with self.engine.begin() as conn:
            conn.execute(
                sql,
                {
                    "news_id": nid,
                    "china_relevance_score": int(china_relevance_score) if china_relevance_score is not None else None,
                    "china_impact_sentiment": float(china_impact_sentiment) if china_impact_sentiment is not None else None,
                    "scoring_evidence": (scoring_evidence or "")[:2048] if scoring_evidence is not None else None,
                    "exact_quotes": (exact_quotes or "")[:8000] if exact_quotes is not None else None,
                    "is_china_related": bool(is_china_related) if is_china_related is not None else None,
                    "category": category,
                    "sub_tags": json.dumps([str(x) for x in (sub_tags or []) if str(x).strip()]) if sub_tags is not None else None,
                    "topic": topic,
                    "impact_level": int(impact_level) if impact_level is not None else None,
                    "entities": entities_json,
                },
            )

    def fetch_centroid_policy(self, news_id: Any) -> str | None:
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        if "news_ingest_meta" not in set(insp.get_table_names()):
            return None
        sql = text("SELECT centroid_policy FROM news_ingest_meta WHERE news_id = :nid LIMIT 1")
        try:
            with self.engine.connect() as conn:
                row = conn.execute(sql, {"nid": news_id}).fetchone()
                if row and row[0] is not None:
                    return str(row[0])
        except Exception:
            logger.exception("fetch_centroid_policy failed news_id=%s", news_id)
        return None

    def fetch_registry_by_numeric_ids(self, numeric_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        if not numeric_ids:
            return {}
        ids_literal = ",".join(str(int(x)) for x in numeric_ids)
        sql = text(
            f"""
            SELECT milvus_numeric_id, cluster_id, member_count, frozen_at, last_article_at, sample_vectors
            FROM micro_cluster_registry
            WHERE milvus_numeric_id IN ({ids_literal})
            """
        )
        out: Dict[int, Dict[str, Any]] = {}
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(sql).fetchall()
                for mid, cid, mcount, frozen, last_at, samples in rows:
                    out[int(mid)] = {
                        "cluster_id": str(cid),
                        "member_count": int(mcount or 0),
                        "frozen_at": frozen,
                        "last_article_at": last_at,
                        "sample_vectors": samples,
                    }
        except Exception:
            logger.exception("fetch_registry_by_numeric_ids failed")
        return out

    def upsert_micro_cluster_registry(
        self,
        *,
        cluster_id: str,
        milvus_numeric_id: int,
        member_count: int,
        parent_cluster_id: str | None = None,
        frozen_at: datetime | None = None,
        centroid_version: int = 1,
        last_article_at: datetime | None = None,
        sample_vectors: Any | None = None,
    ) -> None:
        sql = text(
            """
            INSERT INTO micro_cluster_registry (
              cluster_id, milvus_numeric_id, member_count, parent_cluster_id,
              frozen_at, centroid_version, last_article_at, sample_vectors, updated_at
            ) VALUES (
              :cluster_id, :milvus_numeric_id, :member_count, :parent_cluster_id,
              :frozen_at, :centroid_version, :last_article_at, CAST(:sample_vectors AS JSONB), NOW()
            )
            ON CONFLICT (cluster_id) DO UPDATE SET
              member_count = EXCLUDED.member_count,
              parent_cluster_id = COALESCE(EXCLUDED.parent_cluster_id, micro_cluster_registry.parent_cluster_id),
              frozen_at = COALESCE(EXCLUDED.frozen_at, micro_cluster_registry.frozen_at),
              centroid_version = EXCLUDED.centroid_version,
              last_article_at = COALESCE(EXCLUDED.last_article_at, micro_cluster_registry.last_article_at),
              sample_vectors = EXCLUDED.sample_vectors,
              updated_at = NOW()
            """
        )
        payload = {
            "cluster_id": cluster_id,
            "milvus_numeric_id": milvus_numeric_id,
            "member_count": member_count,
            "parent_cluster_id": parent_cluster_id,
            "frozen_at": frozen_at,
            "centroid_version": centroid_version,
            "last_article_at": last_article_at,
            "sample_vectors": json.dumps(sample_vectors) if sample_vectors is not None else None,
        }
        with self.engine.begin() as conn:
            conn.execute(sql, payload)

    def freeze_micro_cluster(self, cluster_id: str) -> None:
        sql = text(
            """
            UPDATE micro_cluster_registry
            SET frozen_at = NOW(), updated_at = NOW()
            WHERE cluster_id = :cid AND frozen_at IS NULL
            """
        )
        with self.engine.begin() as conn:
            conn.execute(sql, {"cid": cluster_id})

    def insert_micro_cluster_lineage(
        self,
        *,
        parent_cluster_id: str,
        child_cluster_id: str,
        event_type: str,
        meta: Dict[str, Any] | None = None,
    ) -> int:
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        if "micro_cluster_lineage" not in set(insp.get_table_names()):
            return -1
        sql = text(
            """
            INSERT INTO micro_cluster_lineage (parent_cluster_id, child_cluster_id, event_type, meta)
            VALUES (:p, :c, :e, CAST(:m AS JSONB))
            RETURNING id
            """
        )
        with self.engine.begin() as conn:
            row = conn.execute(
                sql,
                {
                    "p": parent_cluster_id,
                    "c": child_cluster_id,
                    "e": event_type,
                    "m": json.dumps(meta) if meta is not None else None,
                },
            ).fetchone()
        return int(row[0]) if row else -1

    def persist_constraint_snapshot(
        self,
        *,
        snapshot_id: str,
        sha256_hex: str,
        row_count: int,
        meta: Dict[str, Any] | None = None,
    ) -> None:
        sql = text(
            """
            INSERT INTO constraint_snapshots (snapshot_id, sha256_hex, source_row_count, meta)
            VALUES (:sid, :sha, :rc, CAST(:meta AS JSONB))
            ON CONFLICT (snapshot_id) DO UPDATE SET
              sha256_hex = EXCLUDED.sha256_hex,
              source_row_count = EXCLUDED.source_row_count,
              meta = EXCLUDED.meta,
              created_at = constraint_snapshots.created_at
            """
        )
        with self.engine.begin() as conn:
            conn.execute(
                sql,
                {
                    "sid": snapshot_id,
                    "sha": sha256_hex,
                    "rc": row_count,
                    "meta": json.dumps(meta) if meta is not None else None,
                },
            )

    def compute_must_not_link_snapshot(self) -> Tuple[str, str, int]:
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        if "graph_must_not_link" not in set(insp.get_table_names()):
            return "mnl-empty", sha256(b"[]").hexdigest(), 0
        sql = text(
            """
            SELECT micro_a_id, micro_b_id
            FROM graph_must_not_link
            WHERE active = TRUE AND (expires_at IS NULL OR expires_at > NOW())
            ORDER BY micro_a_id ASC, micro_b_id ASC
            """
        )
        rows: List[Tuple[str, str]] = []
        with self.engine.connect() as conn:
            for a, b in conn.execute(sql):
                rows.append((str(a), str(b)))
        blob = json.dumps(rows, ensure_ascii=True).encode("utf-8")
        digest = sha256(blob).hexdigest()
        snapshot_id = f"mnl-{digest[:16]}"
        return snapshot_id, digest, len(rows)

    def fetch_must_not_link_blocked_pairs(self) -> Set[Tuple[str, str]]:
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        if "graph_must_not_link" not in set(insp.get_table_names()):
            return set()
        sql = text(
            """
            SELECT micro_a_id, micro_b_id
            FROM graph_must_not_link
            WHERE active = TRUE AND (expires_at IS NULL OR expires_at > NOW())
            """
        )
        blocked: Set[Tuple[str, str]] = set()
        with self.engine.connect() as conn:
            for a, b in conn.execute(sql):
                ca, cb = sorted((str(a), str(b)))
                blocked.add((ca, cb))
        return blocked

    def fetch_micro_cluster_windows(self) -> Dict[str, Any]:
        sql = text(
            """
            SELECT cluster_id::text, last_article_at
            FROM micro_cluster_registry
            WHERE frozen_at IS NULL
            """
        )
        out: Dict[str, Any] = {}
        with self.engine.connect() as conn:
            for cid, ts in conn.execute(sql):
                out[str(cid)] = ts
        return out

    def fetch_cluster_entity_sets(self) -> Dict[str, Set[str]]:
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        if "news_ai_analysis" not in set(insp.get_table_names()):
            return {}
        cols = {c["name"] for c in insp.get_columns("news_ai_analysis")}
        if "entities" not in cols:
            return {}

        # Extract `text` from JSONB array-of-objects, normalized to lower() for stable clustering.
        sql = text(
            """
            SELECT DISTINCT
              na.cluster_id::text AS cid,
              lower(trim(COALESCE(ent.elem->>'text', ''))) AS ent
            FROM news_ai_analysis na
            CROSS JOIN LATERAL jsonb_array_elements(COALESCE(na.entities, '[]'::jsonb)) AS ent(elem)
            WHERE na.cluster_id IS NOT NULL
              AND na.cluster_id::text <> 'SKIP_EMA'
              AND trim(COALESCE(ent.elem->>'text', '')) <> ''
            """
        )
        buckets: Dict[str, Set[str]] = {}
        with self.engine.connect() as conn:
            for cid, ent in conn.execute(sql):
                cid_s = str(cid)
                buckets.setdefault(cid_s, set()).add(str(ent))
        return buckets

    def replace_macro_graph_edges_m0(
        self,
        *,
        edges: List[Dict[str, Any]],
        constraint_snapshot_id: str,
    ) -> int:
        return self.replace_macro_graph_edges_by_maturity(
            edges=edges,
            constraint_snapshot_id=constraint_snapshot_id,
            maturity="m0",
        )

    def replace_macro_graph_edges_by_maturity(
        self,
        *,
        edges: List[Dict[str, Any]],
        constraint_snapshot_id: str,
        maturity: str,
    ) -> int:
        from sqlalchemy import inspect as sa_inspect

        insp = sa_inspect(self.engine)
        if "macro_graph_edges_m0" not in set(insp.get_table_names()):
            return 0
        del_sql = text("DELETE FROM macro_graph_edges_m0 WHERE maturity = :m")
        ins_sql = text(
            """
            INSERT INTO macro_graph_edges_m0 (
              micro_a_id, micro_b_id, jaccard, weight, maturity, constraint_snapshot_id, meta
            ) VALUES (
              :a, :b, :j, :w, :mat, :csid, CAST(:meta AS JSONB)
            )
            """
        )
        written = 0
        with self.engine.begin() as conn:
            conn.execute(del_sql, {"m": maturity})
            for e in edges:
                conn.execute(
                    ins_sql,
                    {
                        "a": e["micro_a_id"],
                        "b": e["micro_b_id"],
                        "j": float(e["jaccard"]),
                        "w": float(e.get("weight", 1.0)),
                        "mat": maturity,
                        "csid": constraint_snapshot_id,
                        "meta": json.dumps(e.get("meta")) if e.get("meta") is not None else None,
                    },
                )
                written += 1
        return written

    def insert_pipeline_dlq(
        self,
        *,
        channel: str = "gateway_predict",
        task_type: str | None = None,
        payload: Dict[str, Any] | None = None,
        error_class: str | None = None,
        error_detail: str | None = None,
        status: str = "pending",
    ) -> int:
        sql = text(
            """
            INSERT INTO pipeline_task_dlq (
              channel, task_type, payload, error_class, error_detail, status, updated_at
            ) VALUES (
              :channel, :task_type, CAST(:payload AS JSONB), :ecls, :edet, :st, NOW()
            )
            RETURNING id
            """
        )
        with self.engine.begin() as conn:
            row = conn.execute(
                sql,
                {
                    "channel": channel,
                    "task_type": task_type,
                    "payload": json.dumps(payload) if payload is not None else None,
                    "ecls": error_class,
                    "edet": error_detail[:8000] if error_detail else None,
                    "st": status,
                },
            ).fetchone()
        return int(row[0]) if row else -1

    def fetch_dlq_row(self, dlq_id: int) -> Dict[str, Any] | None:
        sql = text(
            """
            SELECT id, channel, task_type, payload, error_class, error_detail, status, retry_count, created_at
            FROM pipeline_task_dlq WHERE id = :id
            """
        )
        with self.engine.connect() as conn:
            row = conn.execute(sql, {"id": dlq_id}).mappings().first()
            if not row:
                return None
            out = dict(row)
            if isinstance(out.get("payload"), str):
                try:
                    out["payload"] = json.loads(out["payload"])
                except Exception:
                    pass
            return out

    def list_dlq(self, *, status: str = "pending", limit: int = 100) -> List[Dict[str, Any]]:
        sql = text(
            """
            SELECT id, channel, task_type, status, retry_count, error_class, created_at
            FROM pipeline_task_dlq
            WHERE status = :st
            ORDER BY id ASC
            LIMIT :lim
            """
        )
        rows: List[Dict[str, Any]] = []
        with self.engine.connect() as conn:
            for r in conn.execute(sql, {"st": status, "lim": limit}).mappings():
                rows.append(dict(r))
        return rows

    def count_pipeline_dlq(self, *, channel: str | None = None, status: str | None = None) -> int:
        where = []
        params: Dict[str, Any] = {}
        if channel is not None:
            where.append("channel = :ch")
            params["ch"] = channel
        if status is not None:
            where.append("status = :st")
            params["st"] = status
        sql = "SELECT COUNT(*) FROM pipeline_task_dlq"
        if where:
            sql += " WHERE " + " AND ".join(where)
        try:
            with self.engine.connect() as conn:
                row = conn.execute(text(sql), params).fetchone()
                return int(row[0]) if row else 0
        except Exception:
            logger.exception("count_pipeline_dlq failed")
            return -1

    def count_pipeline_dlq_for_handoff_task(
        self,
        *,
        channel: str,
        task_type: str,
        handoff_id: int,
    ) -> int:
        sql = text(
            """
            SELECT COUNT(*)
            FROM pipeline_task_dlq
            WHERE channel = :ch
              AND task_type = :tt
              AND (payload->>'handoff_id') = :hid
            """
        )
        try:
            with self.engine.connect() as conn:
                row = conn.execute(sql, {"ch": channel, "tt": task_type, "hid": str(int(handoff_id))}).fetchone()
                return int(row[0]) if row else 0
        except Exception:
            logger.exception("count_pipeline_dlq_for_handoff_task failed")
            return -1

    def update_dlq_row(
        self,
        dlq_id: int,
        *,
        status: str,
        error_detail: str | None = None,
        increment_retry: bool = False,
    ) -> None:
        with self.engine.begin() as conn:
            if increment_retry:
                sql = text(
                    """
                    UPDATE pipeline_task_dlq
                    SET status = :st,
                        retry_count = retry_count + 1,
                        error_detail = COALESCE(:ed, error_detail),
                        updated_at = NOW()
                    WHERE id = :id
                    """
                )
                conn.execute(sql, {"id": dlq_id, "st": status, "ed": error_detail})
            elif error_detail is not None:
                sql = text(
                    """
                    UPDATE pipeline_task_dlq
                    SET status = :st, error_detail = :ed, updated_at = NOW()
                    WHERE id = :id
                    """
                )
                conn.execute(sql, {"id": dlq_id, "st": status, "ed": error_detail})
            else:
                sql = text(
                    """
                    UPDATE pipeline_task_dlq
                    SET status = :st, updated_at = NOW()
                    WHERE id = :id
                    """
                )
                conn.execute(sql, {"id": dlq_id, "st": status})

    def fetch_cluster_id_by_milvus_numeric(self, milvus_numeric_id: int) -> str | None:
        sql = text(
            "SELECT cluster_id::text FROM micro_cluster_registry WHERE milvus_numeric_id = :n LIMIT 1"
        )
        with self.engine.connect() as conn:
            row = conn.execute(sql, {"n": milvus_numeric_id}).fetchone()
            return str(row[0]) if row else None


def _milvus_lite_local_file(uri: str) -> bool:
    """Milvus Lite (``*.db`` / ``*.sqlite``) only supports FLAT / IVF_FLAT / AUTOINDEX — not HNSW."""
    try:
        return Path(uri).suffix.lower() in {".db", ".sqlite"}
    except Exception:
        return False


class MilvusManager:
    def __init__(self, collection_name: str = "micro_clusters", dim: int = 1024) -> None:
        self.settings = get_settings()
        self.collection_name = collection_name
        self.dim = dim
        try:
            from pathlib import Path

            mu = Path(self.settings.milvus_uri)
            if mu.suffix.lower() in {".db", ".sqlite"}:
                mu.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self.client = MilvusClient(uri=self.settings.milvus_uri)
        self._search_count = 0
        self._search_elapsed = 0.0
        self._upsert_count = 0
        self._upsert_elapsed = 0.0
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        try:
            if self.client.has_collection(self.collection_name):
                return
            schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
            schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=self.dim)
            schema.add_field(field_name="updated_at", datatype=DataType.INT64)

            index_params = self.client.prepare_index_params()
            if _milvus_lite_local_file(self.settings.milvus_uri):
                index_params.add_index(
                    field_name="vector",
                    index_type="FLAT",
                    metric_type="COSINE",
                    params={},
                )
            else:
                index_params.add_index(
                    field_name="vector",
                    index_type="HNSW",
                    metric_type="COSINE",
                    params={"M": 16, "efConstruction": 200},
                )
            self.client.create_collection(
                collection_name=self.collection_name,
                schema=schema,
                index_params=index_params,
            )
            logger.info("milvus collection created name=%s", self.collection_name)
        except Exception:
            logger.exception("milvus init collection failed")
            raise

    def search_nearest_cluster(self, vector: List[float], limit: int = 5) -> List[Dict[str, Any]]:
        started = perf_counter()
        try:
            results = self.client.search(
                collection_name=self.collection_name,
                data=[vector],
                anns_field="vector",
                limit=limit,
                output_fields=["updated_at"],
            )
            elapsed = perf_counter() - started
            self._search_count += 1
            self._search_elapsed += elapsed
            if self._search_count % 1000 == 0:
                avg = self._search_elapsed / max(self._search_count / 1000, 1)
                logger.info(
                    "milvus search progress=%d avg_latency_per_1k=%.4fs",
                    self._search_count,
                    avg,
                )
            else:
                logger.info("milvus search done limit=%d elapsed=%.4fs", limit, elapsed)
            if not results:
                return []
            return results[0]
        except Exception:
            logger.exception("milvus search failed")
            raise

    def search_centroid_neighbors(
        self,
        vector: List[float],
        limit: int,
        output_fields: List[str] | None = None,
    ) -> List[Dict[str, Any]]:
        """Return raw neighbor hits including centroid ``vector`` for Phase-1 routing."""
        fields = output_fields or ["vector", "updated_at"]
        started = perf_counter()
        try:
            results = self.client.search(
                collection_name=self.collection_name,
                data=[vector],
                anns_field="vector",
                limit=limit,
                output_fields=fields,
            )
            elapsed = perf_counter() - started
            self._search_count += 1
            self._search_elapsed += elapsed
            if not results:
                return []
            return list(results[0])
        except Exception:
            logger.exception("milvus detailed search failed")
            raise

    def metrics_snapshot(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "collection": self.collection_name,
            "dim": self.dim,
            "search_calls": self._search_count,
            "search_total_latency_s": round(self._search_elapsed, 6),
            "search_avg_latency_s": round(self._search_elapsed / max(self._search_count, 1), 8),
            "upsert_calls": self._upsert_count,
            "upsert_total_latency_s": round(self._upsert_elapsed, 6),
            "upsert_avg_latency_s": round(self._upsert_elapsed / max(self._upsert_count, 1), 8),
            "index_profile": (
                {
                    "ann_field": "vector",
                    "metric_type": "COSINE",
                    "index_type": "FLAT",
                    "params": {},
                }
                if _milvus_lite_local_file(self.settings.milvus_uri)
                else {
                    "ann_field": "vector",
                    "metric_type": "COSINE",
                    "index_type": "HNSW",
                    "params": {"M": 16, "efConstruction": 200},
                }
            ),
        }
        try:
            desc = getattr(self.client, "describe_collection", None)
            if callable(desc):
                out["describe_collection"] = desc(self.collection_name)
        except Exception:
            pass
        try:
            stats = getattr(self.client, "get_collection_stats", None)
            if callable(stats):
                out["collection_stats"] = stats(self.collection_name)
        except Exception:
            pass
        return out

    @staticmethod
    def _cluster_id_to_int(cluster_id: Any) -> int:
        if isinstance(cluster_id, int):
            return cluster_id
        text_id = str(cluster_id)
        return int(md5(text_id.encode("utf-8")).hexdigest()[:15], 16)

    def numeric_id(self, cluster_id: Any) -> int:
        return self._cluster_id_to_int(cluster_id)

    def upsert_cluster_centroid(self, cluster_id: Any, new_vector: List[float]) -> None:
        started = perf_counter()
        try:
            numeric_cluster_id = self._cluster_id_to_int(cluster_id)
            now_ts = int(datetime.now(tz=timezone.utc).timestamp())
            self.client.upsert(
                collection_name=self.collection_name,
                data=[
                    {
                        "id": numeric_cluster_id,
                        "vector": new_vector,
                        "updated_at": now_ts,
                    }
                ],
            )
            elapsed = perf_counter() - started
            self._upsert_count += 1
            self._upsert_elapsed += elapsed
            logger.info("milvus centroid upsert cluster_id=%s elapsed=%.4fs", cluster_id, elapsed)
        except Exception:
            logger.exception("milvus centroid upsert failed cluster_id=%s", cluster_id)
            raise
