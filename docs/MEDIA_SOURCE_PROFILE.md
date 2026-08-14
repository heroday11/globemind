# Media Source Profile

创建时间：2026-06-26

## 目的

`media_source_profile` 是 GlobeMind 后续做 Ground-News-style 新闻聚合、来源构成、地域构成、媒体类型构成、政治倾向比例的基础维表。

当前阶段只做“可验证的基础画像”和“待审核字段占位”。政治倾向只在结构明确的官方/国有来源或有外部评级证据时填充。

## 文件和表

- CSV：`data/source_curation/media_source_profile.csv`
- DB：`news.public.media_source_profile`
- 生成脚本：`scripts/build_media_source_profile_seed.py`
- 校验脚本：`scripts/validate_media_source_profile.py`
- 结构性标签脚本：`scripts/apply_structural_media_profile_labels.py`
- 外部评级/机构性覆盖脚本：`scripts/apply_ground_news_rating_overrides.py`
- 地缘分组规则脚本：`scripts/apply_geo_alignment_rules.py`
- 上游来源清单：`data/source_curation/historical_wave1_targets.csv`

## 当前状态

截至 2026-06-26 20:58 CST：

- `domain` 覆盖：100/100
- `source_name` 覆盖：100/100
- `country` 覆盖：100/100
- `source_type` 覆盖：100/100
- `ownership_type` 已知：100/100
- `geo_alignment` 已知：100/100
- `political_leaning` 已知：83/100
- `credibility_tier` 已知：82/100
- `review_status=reviewed`：89/100
- `review_status=needs_review`：11/100
- `review_status=seeded`：0/100
- `public.news` join `media_source_profile`：缺失 0 行

本轮补充文件：

- `data/source_curation/media_source_review_queue_20260626_round2.csv`

本轮原则：

- 所有制字段优先补齐到可用于来源构成统计的粒度。
- 政治倾向和可信度只在有 Ground News、MBFC、AllSides 等外部评级证据时更新。
- 找不到明确第三方评级的来源保留 `unknown`，并进入剩余审核队列，而不是强行归为 `center`。

## 字段说明

| 字段 | 中文名 | 当前来源/规则 |
|---|---|---|
| `domain` | 媒体域名 | `public.media_source.domain` |
| `site_id` | 来源站点 ID | `historical_wave1_targets.csv` |
| `source_name` | 媒体/机构显示名 | 由 `site_id` 生成，并用人工 override 修正 |
| `country` | 媒体/机构所属国家或辖区 | 人工 seed override |
| `region` | 区域 | `historical_wave1_targets.csv` |
| `region_code` | 区域代码 | DB 已有值或由 `region` 映射 |
| `source_type` | 来源类型 | `historical_wave1_targets.csv` |
| `layer` | 信号层级 | `historical_wave1_targets.csv` |
| `priority_tier` | 采集优先级 | `historical_wave1_targets.csv` |
| `ownership_type` | 所有制/机构类型 | 仅对官方、国际组织、公共广播、国有媒体、通讯社做结构性推断 |
| `geo_alignment` | 地缘阵营标签 | 由已校验 `country/region` 做确定性分组；用于来源构成，不等同政治倾向 |
| `political_leaning` | 政治倾向 | 有第三方评级或结构性党政/国有证据时填充，禁止无证据自动填充 |
| `credibility_tier` | 可信度等级 | 有 Ground News/MBFC/结构性证据时填充 |
| `label_confidence` | 标签置信度 | 结构性字段为 `high/medium`，普通媒体 seed 为 `low` |
| `evidence_url` | 证据链接 | 当前为 seed origin，政治倾向等高风险字段需要专门证据 |
| `evidence_note` | 证据说明 | 记录 seed 和结构性推断来源 |
| `review_status` | 审核状态 | `seeded` / `needs_review` / `reviewed` / `locked` |
| `article_count_snapshot` | 当前文章量快照 | `news` 表按 `media_source_id` 聚合 |
| `profile_version` | 画像版本 | 当前为 `media_profile_seed_v1` |
| `updated_at` | 更新时间 | 生成脚本写入 |

## 准确性规则

1. `source_type`、`layer`、`priority_tier`、`region` 来自现有 source curation 清单，可直接用于粗粒度统计。
2. `source_name` 和 `country` 是人工 seed，适合展示和分组，但后续正式产品可继续人工校对。
3. `ownership_type` 只对结构明确的来源自动填：政府、国际组织、公共广播、国有媒体、通讯社；普通商业/民营媒体除非有明确证据，否则保持 `unknown`。
4. `political_leaning`、`credibility_tier` 不做 LLM 猜测。非 `unknown` 值必须有 `evidence_url` 或结构性证据，并设置为 `reviewed` / `locked` / `needs_review`。
5. `geo_alignment` 只用于地缘/区域构成分析，按已校验 `country/region` 分组；它不是媒体政治倾向标签。
6. 后续计算“政治倾向比例”时，只应对 `reviewed/locked` 且非 `unknown` 的来源单独标注覆盖率，不要把 unknown 当成中立。

## 常用命令

重建 CSV 并写入 DB：

```bash
/root/data/globemind/.env_torch/bin/python scripts/build_media_source_profile_seed.py --write-db
```

校验 CSV 和 DB 域名覆盖：

```bash
/root/data/globemind/.env_torch/bin/python scripts/validate_media_source_profile.py --check-db-domains
```

当前校验会检查：

- 必要列存在
- 基础字段非空
- 枚举值合法
- `article_count_snapshot` 为非负整数
- `evidence_url` 为 HTTP(S) URL
- `political_leaning != unknown` 时必须有证据链接
- DB 当前媒体域名全部存在于 profile

应用结构性标签并同步 CSV：

```bash
/root/data/globemind/.env_torch/bin/python scripts/apply_structural_media_profile_labels.py --write-db --export-csv
```

应用外部评级/机构性覆盖并同步 CSV：

```bash
/root/data/globemind/.env_torch/bin/python scripts/apply_ground_news_rating_overrides.py --write-db --export-csv
```

按已校验国家/区域补齐地缘分组并同步 CSV：

```bash
/root/data/globemind/.env_torch/bin/python scripts/apply_geo_alignment_rules.py --write-db --export-csv
```

下游 join 方式：

```sql
SELECT
  n.id,
  n.title,
  n.published_at,
  ms.domain,
  msp.source_name,
  msp.country,
  msp.source_type,
  msp.ownership_type,
  msp.political_leaning,
  msp.review_status
FROM public.news n
JOIN public.media_source ms ON ms.id = n.media_source_id
LEFT JOIN public.media_source_profile msp ON msp.domain = ms.domain;
```

## 后续处理建议

剩余 P0 高影响来源主要是缺可靠第三方可信度/政治倾向评级证据的区域媒体：

- `bangkokbiznews.com`
- `businesstimes.com.sg`
- `zaobao.com`
- `haberturk.com`

当前剩余审核队列：

- `data/source_curation/media_source_review_queue.csv`
- 共 19 行，其中 P0 4 行、P1 7 行、P2 8 行

下一步优先按 `article_count_snapshot DESC` 审核剩余队列，补齐：

- `political_leaning`
- `credibility_tier`
- `evidence_url`
- `evidence_note`
- `review_status`

完成第一批审核后，L1/L2 聚类结果就可以做来源构成统计：同一 story cluster 内按 `source_type`、`country`、`ownership_type`、`political_leaning` 聚合，并同时报告 unknown 覆盖率。
