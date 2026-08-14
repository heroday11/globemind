# GlobeMind DB Schema (Live)

Auto-generated from current PostgreSQL `public` schema.

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

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `id` | bigint (int8) | NO | nextval('analysis_lineage_id_seq'::regclass) | - | NO |  | 1 |
| `run_id` | uuid (uuid) | NO |  | - | NO |  | d51facf4-2fdb-4f9a-8450-e0f898fc6469 |
| `pipeline_version` | character varying (varchar) | NO |  | - | NO |  | globemind-rules-v0 |
| `model_manifest` | jsonb (jsonb) | YES |  | - | NO |  | {"ruleset_version": "globemind-rules-v0", "fast_track_rules_sha256_16": "0931fae... |
| `input_content_hash` | character varying (varchar) | YES |  | - | NO |  |  |
| `constraint_snapshot_id` | character varying (varchar) | YES |  | - | NO |  | mnl-4f53cda18c2baa0c |
| `output_entity_id` | character varying (varchar) | YES |  | - | NO |  | macro_m0_batch |
| `operator` | character varying (varchar) | NO | 'system'::character varying | - | NO |  | system |
| `superseded_by` | bigint (int8) | YES |  | - | NO |  |  |
| `meta` | jsonb (jsonb) | YES |  | - | NO |  | {"edges": 0, "clusters_tagged": 0} |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  | 2026-04-24 00:10:17.291561+00 |

## `app_user`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `id` | integer (int4) | NO | nextval('app_user_id_seq'::regclass) | - | NO |  | 1 |
| `username` | character varying (varchar) | NO |  | - | NO |  | heroday |
| `password_hash` | character varying (varchar) | NO |  | - | NO |  | $2b$12$gQW2puvc1j55HzHicKGLvOWwZuMpBqvuuqQJbmXYJXug1ryL23ujm |
| `created_at` | timestamp without time zone (timestamp) | NO | CURRENT_TIMESTAMP | - | NO |  | 2026-03-08 23:07:56.704108 |
| `full_name` | character varying (varchar) | YES |  | - | NO |  | E2E User 2 |
| `email` | character varying (varchar) | YES |  | - | NO |  | e2e_1773058129@example.com |
| `phone` | character varying (varchar) | YES |  | - | NO |  | 13973058129 |
| `updated_at` | timestamp without time zone (timestamp) | YES |  | - | NO |  | 2026-03-09 12:08:52.857464 |

## `constraint_snapshots`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `snapshot_id` | character varying (varchar) | NO |  | - | NO |  | mnl-4f53cda18c2baa0c |
| `sha256_hex` | character varying (varchar) | NO |  | - | NO |  | 4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945 |
| `source_row_count` | integer (int4) | NO | 0 | - | NO |  | 0 |
| `meta` | jsonb (jsonb) | YES |  | - | NO |  | {"source": "graph_must_not_link"} |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  | 2026-04-24 00:10:17.278332+00 |

## `country`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `id` | integer (int4) | NO |  | - | NO | 自增ID | 984 |
| `c_name` | text (text) | YES |  | - | NO | 国家名称 | 阿富汗 |
| `e_name` | character varying (varchar) | YES |  | - | NO | 英文名 | Afghanistan |
| `created_time` | timestamp without time zone (timestamp) | YES |  | - | NO | 每当每行的数据被创建的时候更新现在的时间 | 2020-10-12 18:00:00 |
| `updated_time` | timestamp without time zone (timestamp) | YES |  | - | NO | 更新时间 | 2020-10-12 18:00:00 |

## `fast_track_events`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `id` | bigint (int8) | NO | nextval('fast_track_events_id_seq'::regclass) | - | NO |  |  |
| `alert_kind` | character varying (varchar) | NO |  | - | NO |  |  |
| `severity` | smallint (int2) | NO | '1'::smallint | - | NO |  |  |
| `news_id` | integer (int4) | YES |  | - | NO |  |  |
| `fingerprint_family_id` | uuid (uuid) | YES |  | - | NO |  |  |
| `rule_hit_ids` | jsonb (jsonb) | YES |  | - | NO |  |  |
| `summary_snippet` | text (text) | YES |  | - | NO |  |  |
| `payload` | jsonb (jsonb) | YES |  | - | NO |  |  |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |  |
| `lineage_run_id` | uuid (uuid) | YES |  | - | NO |  |  |

## `language`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `id` | integer (int4) | NO |  | - | NO | 自增ID | 1727 |
| `name` | text (text) | YES |  | - | NO | 英文名 | Afar |
| `ISO_639_2` | text (text) | YES |  | - | NO | ISO 639-2 编码 | aar |
| `ISO_639_1` | text (text) | YES |  | - | NO | ISO 639-1 编码 | aa |
| `updated_time` | timestamp without time zone (timestamp) | YES |  | - | NO | 更新时间 | 2020-10-12 18:00:00 |
| `created_time` | timestamp without time zone (timestamp) | YES |  | - | NO | 每当每行的数据被创建的时候更新现在的时间 | 2020-10-12 18:00:00 |

## `micro_cluster_registry`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `cluster_id` | character varying (varchar) | NO |  | - | NO |  | a51d2087-14fe-4558-9c34-e8e16e44a007 |
| `milvus_numeric_id` | bigint (int8) | NO |  | - | NO |  | 1100474097694264762 |
| `member_count` | integer (int4) | NO | 0 | - | NO |  | 1 |
| `parent_cluster_id` | character varying (varchar) | YES |  | - | NO |  |  |
| `frozen_at` | timestamp with time zone (timestamptz) | YES |  | - | NO |  |  |
| `centroid_version` | integer (int4) | NO | 1 | - | NO |  | 1 |
| `last_article_at` | timestamp with time zone (timestamptz) | YES |  | - | NO |  | 2026-04-23 23:42:35.791597+00 |
| `sample_vectors` | jsonb (jsonb) | YES |  | - | NO |  | [[-0.004426097663135305, -0.005311169900275143, -0.0034193472677710806, 0.021563... |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  | 2026-04-23 23:42:35.890023+00 |
| `updated_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  | 2026-04-23 23:42:35.890023+00 |

## `news`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `id` | integer (int4) | NO |  | - | YES | 新闻自身的id，自增 | 21764534 |
| `website_id` | integer (int4) | YES |  | - | NO | 外键：新闻表网站地址id | 1970 |
| `request_url` | text (text) | NO |  | - | NO | 新闻的请求链接 | https://www.donanimhaber.com//ticimax-belcikali-teknoloji-sirketine-satildi--192... |
| `response_url` | text (text) | YES |  | - | NO | 新闻网站的响应链接 | https://www.donanimhaber.com//ticimax-belcikali-teknoloji-sirketine-satildi--192... |
| `category1` | text (text) | YES |  | - | NO | 一级类别 | Girişim |
| `category2` | text (text) | YES |  | - | NO | 二级类别 | Güncel Açıklamalar |
| `title` | text (text) | YES |  | - | NO | 标题 | Android'in Telefon uygulamasına iPhone benzeri Arama Kartları özelliği geldi |
| `body` | text (text) | YES |  | - | NO | 正文 | Tam Boyutta GörGoogle, Android'in Telefon uygulamasına “Arama Kartları” (Calling... |
| `pub_time` | timestamp without time zone (timestamp) | YES |  | - | NO | 发布时间例2017-01-01 00:00:00,
没有发布时间的则为0000-00-00 00:00:00 | 2025-08-29 00:00:00 |
| `cole_time` | timestamp without time zone (timestamp) | YES |  | - | NO | 爬虫时间  年-月-日 时:分:秒 | 2025-09-14 22:48:35 |
| `images` | text (text) | YES |  | - | NO | 新闻图片列表，使用json的[]列表，没有则为NULL | ["https://www.donanimhaber.com/images/images/haber/195551/src/android-in-telefon... |
| `language_id` | integer (int4) | YES |  | - | NO | 外键：语音表的ID | 2227 |
| `md5` | character (bpchar) | NO |  | - | NO | 8-24 bit | 26222fa14f0f2d0d3fc8309dbaeff567 |
| `v3_media_id` | integer (int4) | YES |  | Phase 2 | YES |  | 189 |

## `news_ai_analysis`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `news_id` | integer (int4) | NO | nextval('news_ai_analysis_news_id_seq'::regclass) | - | NO |  | 21746220 |
| `cluster_id` | character varying (varchar) | YES |  | - | NO |  |  |
| `analyzed_at` | timestamp without time zone (timestamp) | NO | now() | - | NO |  | 2026-04-28 03:03:39.756852 |
| `entities` | jsonb (jsonb) | YES |  | - | YES |  | [{"role": "UNKNOWN", "text": "维克拉姆·马西", "type": "PER", "relevance": 0.8}] |
| `is_china_related` | boolean (bool) | YES |  | - | NO |  | false |
| `category` | text (text) | YES |  | - | NO |  | 娱乐与文化 |
| `topic` | text (text) | YES |  | - | NO |  | 演员维克拉姆·马西宣布退休 |
| `impact_level` | smallint (int2) | YES |  | - | NO |  | 2 |
| `sub_tags` | jsonb (jsonb) | YES |  | - | YES |  | ["影视明星", "演艺生涯"] |
| `china_relevance_score` | smallint (int2) | YES |  | - | NO |  | 0 |
| `china_impact_sentiment` | real (float4) | YES |  | - | NO |  | 0 |
| `scoring_evidence` | text (text) | YES |  | - | NO |  | 未提取到直接相关字词 |
| `entity_pair_sentiments` | jsonb (jsonb) | YES |  | Phase 1 | YES |  | [] |
| `exact_quotes` | text (text) | YES |  | - | NO |  | Meanwhile, the deadly virus that first originated in the Chinese city of Wuhan i... |

## `news_timestamp_repair_queue`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `id` | bigint (int8) | NO | nextval('news_timestamp_repair_queue_id_seq'::regclass) | - | NO |  |  |
| `news_id` | integer (int4) | NO |  | - | NO |  |  |
| `status` | character varying (varchar) | NO | 'pending'::character varying | - | NO |  |  |
| `reason_code` | character varying (varchar) | NO | 'missing_business_time'::character varying | - | NO |  |  |
| `notes` | text (text) | YES |  | - | NO |  |  |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |  |
| `updated_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |  |

## `news_translation`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `news_id` | integer (int4) | NO |  | - | NO |  | 21758283 |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  | 2026-04-28 06:02:48.683442+00 |
| `updated_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  | 2026-04-28 06:02:48.683442+00 |
| `title` | text (text) | YES |  | - | YES |  | 开商银行预计本周泰铢波动区间为31.20-32.00，关注美联储利率走向和黄金价格 |
| `body` | text (text) | YES |  | - | YES |  | 泰国开商银行(KBANK)预计本周（9月15日至19日）泰铢汇率将在31.20至32.00之间波动。上周五（9月12日）收盘时，泰铢兑美元汇率为31.71。本周... |
| `translation_quality` | character varying (varchar) | NO | 'full'::character varying | - | NO |  | full |

## `pipeline_task_dlq`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `id` | bigint (int8) | NO | nextval('pipeline_task_dlq_id_seq'::regclass) | - | NO |  | 1 |
| `channel` | character varying (varchar) | NO | 'gateway_predict'::character varying | - | NO |  | phase2_enrich_worker |
| `task_type` | character varying (varchar) | YES |  | - | NO |  | ner |
| `payload` | jsonb (jsonb) | YES |  | - | NO |  | {"news_id": 21798237, "request": {"text": "SK hynix, yüksek hızlı 238 katmanlı 4... |
| `error_class` | character varying (varchar) | YES |  | - | NO |  | phase2_dispatch_error |
| `error_detail` | text (text) | YES |  | - | NO |  | [WinError 10061] 由于目标计算机积极拒绝，无法连接。 |
| `status` | character varying (varchar) | NO | 'pending'::character varying | - | NO |  | pending |
| `retry_count` | integer (int4) | NO | 0 | - | NO |  | 0 |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  | 2026-04-24 10:41:00.648906+00 |
| `updated_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  | 2026-04-24 10:41:00.648906+00 |

## `slow_track_handoff`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `id` | bigint (int8) | NO | nextval('slow_track_handoff_id_seq'::regclass) | - | NO |  | 845 |
| `news_id` | integer (int4) | NO |  | - | NO |  | 21797399 |
| `fast_track_event_id` | bigint (int8) | YES |  | - | NO |  |  |
| `lineage_run_id` | uuid (uuid) | YES |  | - | NO |  |  |
| `priority` | integer (int4) | NO | 100 | - | NO |  | 100 |
| `status` | character varying (varchar) | NO | 'pending'::character varying | - | NO |  | done |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  | 2026-04-24 23:23:04.023236+00 |

## `source_domain_tier`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `id` | integer (int4) | NO | nextval('source_domain_tier_id_seq'::regclass) | - | NO |  | 1 |
| `domain_pattern` | character varying (varchar) | NO |  | - | NO |  | reuters.com |
| `tier` | smallint (int2) | NO |  | - | NO |  | 0 |
| `priority` | integer (int4) | NO | 0 | - | NO |  | 100 |
| `notes` | text (text) | YES |  | - | NO |  | seed |
| `active` | boolean (bool) | NO | true | - | NO |  | true |

## `v3_cluster_run`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `run_id` | uuid (uuid) | NO |  | - | NO |  |  |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |  |
| `params` | jsonb (jsonb) | NO | '{}'::jsonb | - | NO |  |  |
| `stats` | jsonb (jsonb) | YES |  | - | NO |  |  |
| `algorithm_version` | text (text) | NO | 'v3_hashing_svd_mbkmeans_v1'::text | - | NO |  |  |

## `v3_gravity_link`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `id` | bigint (int8) | NO | nextval('v3_gravity_link_id_seq'::regclass) | - | NO |  | 1 |
| `source_article_id` | bigint (int8) | NO |  | - | NO |  | 21747296 |
| `target_article_id` | bigint (int8) | NO |  | - | NO |  | 21782659 |
| `weight` | real (float4) | YES |  | - | NO |  | 0.49693254 |
| `s_ent` | real (float4) | YES |  | - | NO |  | 0.25 |
| `s_tag` | real (float4) | YES |  | - | NO |  | 1 |
| `time_decay` | real (float4) | YES |  | - | NO |  | 0.9938651 |
| `action_gate` | real (float4) | NO | 0.8 | - | NO |  | 0.8 |
| `created_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  | 2026-04-27 19:35:28.537074+00 |

## `v3_macro_community`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `macro_id` | text (text) | NO |  | - | NO |  |  |
| `run_id` | uuid (uuid) | NO |  | - | NO |  |  |
| `meta_id` | text (text) | NO |  | - | NO |  |  |
| `label` | text (text) | YES |  | - | NO |  |  |
| `member_count` | integer (int4) | NO | 0 | - | NO |  |  |
| `top_entities` | jsonb (jsonb) | YES |  | - | NO |  |  |
| `timeline_stats` | jsonb (jsonb) | YES |  | - | NO |  |  |

## `v3_media`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `media_id` | integer (int4) | NO | nextval('v3_media_media_id_seq'::regclass) | Phase 2 | NO |  | 1 |
| `name` | character varying (varchar) | NO |  | Phase 2 | NO |  | Reuters |
| `domain` | character varying (varchar) | NO |  | Phase 2 | NO |  | reuters.com |
| `country` | character varying (varchar) | YES |  | Phase 2 | NO |  | UK |
| `political_leaning` | character varying (varchar) | NO |  | Phase 2 | YES |  | western |
| `credibility` | real (float4) | NO | 0.5 | Phase 2 | NO |  | 0.85 |
| `coverage_region` | character varying (varchar) | YES |  | Phase 2 | NO |  | global |

## `v3_meta_community`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `meta_id` | text (text) | NO |  | - | NO |  |  |
| `run_id` | uuid (uuid) | NO |  | - | NO |  |  |
| `label` | text (text) | YES |  | - | NO |  |  |
| `summary` | jsonb (jsonb) | YES |  | - | NO |  |  |
| `member_macro_count` | integer (int4) | NO | 0 | - | NO |  |  |

## `v3_news_community`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `news_id` | bigint (int8) | NO |  | - | NO |  |  |
| `run_id` | uuid (uuid) | NO |  | - | NO |  |  |
| `macro_id` | text (text) | NO |  | - | NO |  |  |
| `meta_id` | text (text) | NO |  | - | NO |  |  |
| `assigned_at` | timestamp with time zone (timestamptz) | NO | now() | - | NO |  |  |

## `website`

| Field | Type | Nullable | Default | Phase | Phase3 Core | Comment | Example |
|---|---|---:|---|---|---:|---|---|
| `id` | integer (int4) | NO |  | - | NO | id，自增，有索引 | 1 |
| `name` | character varying (varchar) | NO |  | - | NO | 爬虫名称 | insideindonesia |
| `country_id` | integer (int4) | YES |  | - | NO | 外键：国家表的国家id | 1027 |
| `language_id` | integer (int4) | YES |  | - | NO | 外键：语言表的语言id | 1866 |
| `user_id` | integer (int4) | YES |  | - | NO |  | 152 |
| `url` | text (text) | YES |  | - | NO | 网站链接,http://开头 | http://www.insideindonesia.org/ |
| `c_name` | text (text) | YES |  | - | NO | 中文名称 | 雅加达环球报 |
| `remark` | text (text) | YES |  | - | NO | 说明本网站在数据获取中的一些问题 | 爬虫error |
| `level` | integer (int4) | YES |  | - | NO | 优先级别 | 999 |
| `status` | character varying (varchar) | YES |  | - | NO |  | RUNNING |
| `created_time` | timestamp without time zone (timestamp) | YES |  | - | NO | 每当每行的数据被创建的时候更新现在的时间 | 2024-03-18 16:13:18 |
| `updated_time` | timestamp without time zone (timestamp) | YES |  | - | NO | 网站入库时间 | 2024-12-04 20:45:10 |

