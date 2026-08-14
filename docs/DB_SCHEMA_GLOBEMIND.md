# GlobeMind Database Schema Reference

Status: current sanitized schema reference
Scope: PostgreSQL `public` schema structure used by GlobeMind
Source: historical schema introspection with all database row values removed

> This document contains table and column structure only. It contains no database row
> values and is not an executable migration or proof that a local/production schema is
> current. Use repository migrations and role-specific runbooks for authorized changes.

## Foreign Key Hints

- `news.website_id` -> `website.id`
- `news.language_id` -> `language.id`
- `news.v3_media_id` -> `v3_media.media_id`
- `news_translation.news_id` -> `news.id`
- `news_ai_analysis.news_id` -> `news.id`
- `slow_track_handoff.news_id` -> `news.id`
- `slow_track_handoff.fast_track_event_id` -> `fast_track_events.id`
- `fast_track_events.news_id` -> `news.id`
- `website.country_id` -> `country.id`
- `website.language_id` -> `language.id`

## Phase Labels

- `Phase 1`: entity-pair sentiment extraction related fields.
- `Phase 2`: media dictionary and media labeling related fields.
- `Phase 3 Core`: fields used by GRAVITY edge construction.

## `analysis_lineage`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `id` | bigint (int8) | NO | nextval('analysis_lineage_id_seq'::regclass) | - | NO |  |
| `run_id` | uuid (uuid) | NO |  | - | NO |  |
| `pipeline_version` | character varying (varchar) | NO |  | - | NO |  |
| `model_manifest` | jsonb (jsonb) | YES |  | - | NO |  |
| `input_content_hash` | character varying (varchar) | YES |  | - | NO |  |
| `constraint_snapshot_id` | character varying (varchar) | YES |  | - | NO |  |
| `output_entity_id` | character varying (varchar) | YES |  | - | NO |  |
| `operator` | character varying (varchar) | NO | 'system'::character varying | - | NO |  |
| `superseded_by` | bigint (int8) | YES |  | - | NO |  |
| `meta` | jsonb (jsonb) | YES |  | - | NO |  |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |

## `app_user`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `id` | integer (int4) | NO | nextval('app_user_id_seq'::regclass) | - | NO |  |
| `username` | character varying (varchar) | NO |  | - | NO |  |
| `password_hash` | character varying (varchar) | NO |  | - | NO |  |
| `created_at` | timestamp without time zone (timestamp) | NO | CURRENT_TIMESTAMP | - | NO |  |
| `full_name` | character varying (varchar) | YES |  | - | NO |  |
| `email` | character varying (varchar) | YES |  | - | NO |  |
| `phone` | character varying (varchar) | YES |  | - | NO |  |
| `updated_at` | timestamp without time zone (timestamp) | YES |  | - | NO |  |

## `constraint_snapshots`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `snapshot_id` | character varying (varchar) | NO |  | - | NO |  |
| `sha256_hex` | character varying (varchar) | NO |  | - | NO |  |
| `source_row_count` | integer (int4) | NO | 0 | - | NO |  |
| `meta` | jsonb (jsonb) | YES |  | - | NO |  |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |

## `country`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `id` | integer (int4) | NO |  | - | NO | 自增ID |
| `c_name` | text (text) | YES |  | - | NO | 国家名称 |
| `e_name` | character varying (varchar) | YES |  | - | NO | 英文名 |
| `created_time` | timestamp without time zone (timestamp) | YES |  | - | NO | 每当每行的数据被创建的时候更新现在的时间 |
| `updated_time` | timestamp without time zone (timestamp) | YES |  | - | NO | 更新时间 |

## `fast_track_events`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `id` | bigint (int8) | NO | nextval('fast_track_events_id_seq'::regclass) | - | NO |  |
| `alert_kind` | character varying (varchar) | NO |  | - | NO |  |
| `severity` | smallint (int2) | NO | '1'::smallint | - | NO |  |
| `news_id` | integer (int4) | YES |  | - | NO |  |
| `fingerprint_family_id` | uuid (uuid) | YES |  | - | NO |  |
| `rule_hit_ids` | jsonb (jsonb) | YES |  | - | NO |  |
| `summary_snippet` | text (text) | YES |  | - | NO |  |
| `payload` | jsonb (jsonb) | YES |  | - | NO |  |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |
| `lineage_run_id` | uuid (uuid) | YES |  | - | NO |  |

## `language`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `id` | integer (int4) | NO |  | - | NO | 自增ID |
| `name` | text (text) | YES |  | - | NO | 英文名 |
| `ISO_639_2` | text (text) | YES |  | - | NO | ISO 639-2 编码 |
| `ISO_639_1` | text (text) | YES |  | - | NO | ISO 639-1 编码 |
| `updated_time` | timestamp without time zone (timestamp) | YES |  | - | NO | 更新时间 |
| `created_time` | timestamp without time zone (timestamp) | YES |  | - | NO | 每当每行的数据被创建的时候更新现在的时间 |

## `micro_cluster_registry`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `cluster_id` | character varying (varchar) | NO |  | - | NO |  |
| `milvus_numeric_id` | bigint (int8) | NO |  | - | NO |  |
| `member_count` | integer (int4) | NO | 0 | - | NO |  |
| `parent_cluster_id` | character varying (varchar) | YES |  | - | NO |  |
| `frozen_at` | timestamp with time zone (timestamptz) | YES |  | - | NO |  |
| `centroid_version` | integer (int4) | NO | 1 | - | NO |  |
| `last_article_at` | timestamp with time zone (timestamptz) | YES |  | - | NO |  |
| `sample_vectors` | jsonb (jsonb) | YES |  | - | NO |  |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |
| `updated_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |

## `news`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `id` | integer (int4) | NO |  | - | YES | 新闻自身的id，自增 |
| `website_id` | integer (int4) | YES |  | - | NO | 外键：新闻表网站地址id |
| `request_url` | text (text) | NO |  | - | NO | 新闻的请求链接 |
| `response_url` | text (text) | YES |  | - | NO | 新闻网站的响应链接 |
| `category1` | text (text) | YES |  | - | NO | 一级类别 |
| `category2` | text (text) | YES |  | - | NO | 二级类别 |
| `title` | text (text) | YES |  | - | NO | 标题 |
| `body` | text (text) | YES |  | - | NO | 正文 |
| `pub_time` | timestamp without time zone (timestamp) | YES |  | - | NO | 新闻发布时间；缺失值遵循历史数据约定 |
| `cole_time` | timestamp without time zone (timestamp) | YES |  | - | NO | 爬虫采集时间 |
| `images` | text (text) | YES |  | - | NO | 新闻图片 JSON 列表；没有图片时为 NULL |
| `language_id` | integer (int4) | YES |  | - | NO | 外键：语言表的 ID |
| `md5` | character (bpchar) | NO |  | - | NO | 内容摘要 |
| `v3_media_id` | integer (int4) | YES |  | Phase 2 | YES |  |

## `news_ai_analysis`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `news_id` | integer (int4) | NO | nextval('news_ai_analysis_news_id_seq'::regclass) | - | NO |  |
| `cluster_id` | character varying (varchar) | YES |  | - | NO |  |
| `analyzed_at` | timestamp without time zone (timestamp) | NO | now() | - | NO |  |
| `entities` | jsonb (jsonb) | YES |  | - | YES |  |
| `is_china_related` | boolean (bool) | YES |  | - | NO |  |
| `category` | text (text) | YES |  | - | NO |  |
| `topic` | text (text) | YES |  | - | NO |  |
| `impact_level` | smallint (int2) | YES |  | - | NO |  |
| `sub_tags` | jsonb (jsonb) | YES |  | - | YES |  |
| `china_relevance_score` | smallint (int2) | YES |  | - | NO |  |
| `china_impact_sentiment` | real (float4) | YES |  | - | NO |  |
| `scoring_evidence` | text (text) | YES |  | - | NO |  |
| `entity_pair_sentiments` | jsonb (jsonb) | YES |  | Phase 1 | YES |  |
| `exact_quotes` | text (text) | YES |  | - | NO |  |

## `news_timestamp_repair_queue`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `id` | bigint (int8) | NO | nextval('news_timestamp_repair_queue_id_seq'::regclass) | - | NO |  |
| `news_id` | integer (int4) | NO |  | - | NO |  |
| `status` | character varying (varchar) | NO | 'pending'::character varying | - | NO |  |
| `reason_code` | character varying (varchar) | NO | 'missing_business_time'::character varying | - | NO |  |
| `notes` | text (text) | YES |  | - | NO |  |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |
| `updated_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |

## `news_translation`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `news_id` | integer (int4) | NO |  | - | NO |  |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |
| `updated_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |
| `title` | text (text) | YES |  | - | YES |  |
| `body` | text (text) | YES |  | - | YES |  |
| `translation_quality` | character varying (varchar) | NO | 'full'::character varying | - | NO |  |

## `pipeline_task_dlq`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `id` | bigint (int8) | NO | nextval('pipeline_task_dlq_id_seq'::regclass) | - | NO |  |
| `channel` | character varying (varchar) | NO | 'gateway_predict'::character varying | - | NO |  |
| `task_type` | character varying (varchar) | YES |  | - | NO |  |
| `payload` | jsonb (jsonb) | YES |  | - | NO |  |
| `error_class` | character varying (varchar) | YES |  | - | NO |  |
| `error_detail` | text (text) | YES |  | - | NO |  |
| `status` | character varying (varchar) | NO | 'pending'::character varying | - | NO |  |
| `retry_count` | integer (int4) | NO | 0 | - | NO |  |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |
| `updated_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |

## `slow_track_handoff`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `id` | bigint (int8) | NO | nextval('slow_track_handoff_id_seq'::regclass) | - | NO |  |
| `news_id` | integer (int4) | NO |  | - | NO |  |
| `fast_track_event_id` | bigint (int8) | YES |  | - | NO |  |
| `lineage_run_id` | uuid (uuid) | YES |  | - | NO |  |
| `priority` | integer (int4) | NO | 100 | - | NO |  |
| `status` | character varying (varchar) | NO | 'pending'::character varying | - | NO |  |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |

## `source_domain_tier`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `id` | integer (int4) | NO | nextval('source_domain_tier_id_seq'::regclass) | - | NO |  |
| `domain_pattern` | character varying (varchar) | NO |  | - | NO |  |
| `tier` | smallint (int2) | NO |  | - | NO |  |
| `priority` | integer (int4) | NO | 0 | - | NO |  |
| `notes` | text (text) | YES |  | - | NO |  |
| `active` | boolean (bool) | NO | true | - | NO |  |

## `v3_cluster_run`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `run_id` | uuid (uuid) | NO |  | - | NO |  |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |
| `params` | jsonb (jsonb) | NO | '{}'::jsonb | - | NO |  |
| `stats` | jsonb (jsonb) | YES |  | - | NO |  |
| `algorithm_version` | text (text) | NO | 'v3_hashing_svd_mbkmeans_v1'::text | - | NO |  |

## `v3_gravity_link`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `id` | bigint (int8) | NO | nextval('v3_gravity_link_id_seq'::regclass) | - | NO |  |
| `source_article_id` | bigint (int8) | NO |  | - | NO |  |
| `target_article_id` | bigint (int8) | NO |  | - | NO |  |
| `weight` | real (float4) | YES |  | - | NO |  |
| `s_ent` | real (float4) | YES |  | - | NO |  |
| `s_tag` | real (float4) | YES |  | - | NO |  |
| `time_decay` | real (float4) | YES |  | - | NO |  |
| `action_gate` | real (float4) | NO | 0.8 | - | NO |  |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |

## `v3_macro_community`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `macro_id` | text (text) | NO |  | - | NO |  |
| `run_id` | uuid (uuid) | NO |  | - | NO |  |
| `meta_id` | text (text) | NO |  | - | NO |  |
| `label` | text (text) | YES |  | - | NO |  |
| `member_count` | integer (int4) | NO | 0 | - | NO |  |
| `top_entities` | jsonb (jsonb) | YES |  | - | NO |  |
| `timeline_stats` | jsonb (jsonb) | YES |  | - | NO |  |

## `v3_media`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `media_id` | integer (int4) | NO | nextval('v3_media_media_id_seq'::regclass) | Phase 2 | NO |  |
| `name` | character varying (varchar) | NO |  | Phase 2 | NO |  |
| `domain` | character varying (varchar) | NO |  | Phase 2 | NO |  |
| `country` | character varying (varchar) | YES |  | Phase 2 | NO |  |
| `political_leaning` | character varying (varchar) | NO |  | Phase 2 | YES |  |
| `credibility` | real (float4) | NO | 0.5 | Phase 2 | NO |  |
| `coverage_region` | character varying (varchar) | YES |  | Phase 2 | NO |  |

## `v3_meta_community`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `meta_id` | text (text) | NO |  | - | NO |  |
| `run_id` | uuid (uuid) | NO |  | - | NO |  |
| `label` | text (text) | YES |  | - | NO |  |
| `summary` | jsonb (jsonb) | YES |  | - | NO |  |
| `member_macro_count` | integer (int4) | NO | 0 | - | NO |  |

## `v3_news_community`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `news_id` | bigint (int8) | NO |  | - | NO |  |
| `run_id` | uuid (uuid) | NO |  | - | NO |  |
| `macro_id` | text (text) | NO |  | - | NO |  |
| `meta_id` | text (text) | NO |  | - | NO |  |
| `assigned_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |

## `website`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment |
|---|---|---:|---|---|---:|---|
| `id` | integer (int4) | NO |  | - | NO | id，自增，有索引 |
| `name` | character varying (varchar) | NO |  | - | NO | 爬虫名称 |
| `country_id` | integer (int4) | YES |  | - | NO | 外键：国家表的国家id |
| `language_id` | integer (int4) | YES |  | - | NO | 外键：语言表的语言id |
| `user_id` | integer (int4) | YES |  | - | NO |  |
| `url` | text (text) | YES |  | - | NO | 网站绝对链接 |
| `c_name` | text (text) | YES |  | - | NO | 中文名称 |
| `remark` | text (text) | YES |  | - | NO | 说明本网站在数据获取中的一些问题 |
| `level` | integer (int4) | YES |  | - | NO | 优先级别 |
| `status` | character varying (varchar) | YES |  | - | NO |  |
| `created_time` | timestamp without time zone (timestamp) | YES |  | - | NO | 每当每行的数据被创建的时候更新现在的时间 |
| `updated_time` | timestamp without time zone (timestamp) | YES |  | - | NO | 网站入库时间 |
