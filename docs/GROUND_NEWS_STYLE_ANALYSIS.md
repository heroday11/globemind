# Ground-News-style Analysis Layer

创建时间：2026-06-26

## 当前目标

把当前历史新闻库逐步补成类似 Ground News 的分析范式：

- 同一事件的多来源聚合
- 来源国家/区域构成
- 来源类型构成
- 所有制/机构类型构成
- 政治倾向比例
- 事实性/可信度覆盖
- blindspot 候选标记

当前已经补好 source profile 基础维表和分析脚手架；正式 story 分析还依赖当前 `news` 库口径下的 L1/L2 聚类输出。

## 当前状态

截至 2026-06-26 20:28 CST：

- `public.news`：约 102 万行，仍在增长
- `media_source_profile`：100/100 来源覆盖
- `source_name/country/source_type`：100% 覆盖
- `ownership_type`：37% 已知
- `geo_alignment`：100% 已知
- `political_leaning`：80% 已知
- `credibility_tier`：81% 已知
- `review_status=reviewed`：80%
- `review_status=needs_review`：1%
- `source_political_ratings_ready/source_factuality_ready/source_reviews_ready`：已过 80% 阈值
- 当前 `news` 库尚无 canonical `story_clusters/story_cluster_members`
- 尚无正式 `story_source_breakdown`

## 新增脚本

### 1. Readiness 检查

```bash
/root/data/globemind/.env_torch/bin/python scripts/check_ground_news_readiness.py
```

用途：

- 检查新闻字段覆盖
- 检查 media profile 覆盖
- 检查新闻表 join profile 是否漏源
- 检查当前 DB 是否已有 story cluster/member 表
- 检查是否已有 story source breakdown
- 列出 Ground-News-style 分析还缺什么

### 2. 导出媒体审核队列

```bash
/root/data/globemind/.env_torch/bin/python scripts/export_media_source_review_queue.py --limit 30
```

输出：

- `data/source_curation/media_source_review_queue.csv`

用途：

- 按 `article_count_snapshot DESC` 导出最高影响媒体源
- 标注缺失字段
- 给人工/证据审核预留 `proposed_*` 字段
- 当前结构性标签、外部评级和地缘规则补全后，前 30 个里 P0 为 4 个

### 3. 导入媒体审核结果

默认 dry-run：

```bash
/root/data/globemind/.env_torch/bin/python scripts/import_media_source_review_queue.py
```

实际写回 DB：

```bash
/root/data/globemind/.env_torch/bin/python scripts/import_media_source_review_queue.py --write-db
```

规则：

- 只读取 `proposed_*` 字段
- 政治倾向和可信度非 `unknown` 时必须提供 HTTP(S) 证据链接
- 默认不会写 DB，必须显式加 `--write-db`
- 写回目标表：`news.public.media_source_profile`

### 4a. 应用结构性媒体标签

```bash
/root/data/globemind/.env_torch/bin/python scripts/apply_structural_media_profile_labels.py --write-db --export-csv
```

用途：

- 官方/国有/国际组织来源：补 `state_aligned`、`geo_alignment`、`credibility_tier`
- 通讯社/公共广播：先补 `credibility_tier`，政治倾向保留 `unknown`
- 普通商业媒体不凭印象填左右倾向

### 4b. 应用外部评级/机构性覆盖

```bash
/root/data/globemind/.env_torch/bin/python scripts/apply_ground_news_rating_overrides.py --write-db --export-csv
```

当前覆盖：

- Ground News 明确评级来源
- MBFC 明确评级来源
- 党政/国有/政府资助关系明确的机构性来源

质量规则：

- 不用 LLM 猜媒体政治倾向。
- 域名或同名来源有歧义时降低 `label_confidence`。
- Ground News 与 MBFC 事实性评级冲突时，按较保守的 `credibility_tier` 写入。
- 结构性 `state_aligned` 只表达机构归属，不等同左右倾向。

### 4c. 应用地缘分组规则

```bash
/root/data/globemind/.env_torch/bin/python scripts/apply_geo_alignment_rules.py --write-db --export-csv
```

用途：

- 只补 `geo_alignment`
- 按已校验 `country/region` 做来源构成分组
- 不改政治倾向、可信度或审核状态

### 5. 生成 story 来源构成

当前需要传入聚类映射：

```bash
/root/data/globemind/.env_torch/bin/python scripts/build_story_source_breakdown.py \
  --mapping /path/to/current_news_story_mapping.jsonl \
  --write-db
```

映射文件支持 JSONL/CSV，字段名支持：

- `cluster_id` 或 `story_id`
- `article_id` 或 `news_id`

输出：

- JSONL：`data/analysis/ground_news/story_source_breakdown.jsonl`
- DB：`news.public.story_source_breakdown`

聚合字段包括：

- `article_count`
- `matched_article_count`
- `source_count`
- `source_domains`
- `representative_title`
- `first_published_at`
- `last_published_at`
- `source_type_counts`
- `source_type_pct_articles`
- `country_counts`
- `ownership_type_counts`
- `credibility_tier_counts`
- `political_leaning_counts_sources`
- `political_group_pct_all_sources`
- `political_group_pct_reviewed_known_sources`
- `analysis_status`

`analysis_status` 目前取值：

- `missing_articles`
- `single_source`
- `missing_political_ratings`
- `low_source_count`
- `ready`

## 正式运行顺序

1. 保持历史爬取继续跑。
2. 应用结构性标签、外部评级/机构性覆盖、地缘分组规则。
3. 审核 `media_source_review_queue.csv` 剩余 P0/P1 高影响来源。
4. 用 `import_media_source_review_queue.py --write-db` 写回人工审核结果。
5. 对当前 `news` 库跑 L1/L2，生成 canonical story/member 映射。
6. 用 `build_story_source_breakdown.py --mapping ... --write-db` 生成来源构成表。
7. 再跑 `check_ground_news_readiness.py`，确认缺口是否收敛。

## 当前剩余缺口

`check_ground_news_readiness.py` 当前只剩两项未就绪：

- canonical `story_clusters/story_cluster_members` 或 `event_coref_clusters/event_coref_members` 表
- `story_source_breakdown` 正式聚合表

媒体画像侧已经过 80% readiness 阈值。剩余 P0 来源：

- `bangkokbiznews.com`
- `businesstimes.com.sg`
- `zaobao.com`
- `haberturk.com`

## 注意

`data/event_coref_mapping_layer1.jsonl` 是旧数据口径，不是当前历史爬取 `news` 库的 canonical 聚类结果。不要把它当作正式 Ground-News-style breakdown 的输入。
