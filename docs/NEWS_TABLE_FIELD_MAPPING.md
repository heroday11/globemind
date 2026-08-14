# News Table Field Mapping

目标表：

- PostgreSQL: `news.public.news`

## 结论

现在按极简口径入库，只保留真正有分析价值、且当前抓取链路能稳定提供的字段。

## 最终写入 `public.news` 的字段

| 数据库字段 | 中文 | 来源 |
|---|---|---|
| `title` | 标题 | `title` |
| `body` | 正文 | `body` |
| `url` | 文章链接 | `response_url / request_url / url` |
| `url_hash` | 链接哈希 | 对最终 `url` 做 `md5` |
| `published_at` | 发布时间 | `published_at` |
| `media_source_id` | 媒体源 ID | 由 `media_source.domain` 解析得到 |
| `language` | 语言短码 | `language` 归一化为短码 |
| `region` | 区域简写 | 源站清单 `region` 转简写 |
| `author` | 作者/署名 | `author` |

## 中间规范化文件保留的辅助字段

| 字段 | 中文 | 说明 |
|---|---|---|
| `media_source_domain` | 媒体域名 | 仅用于查找/创建 `media_source_id` |

## 不再保留的字段

- `abstract`
- `ingested_at`
- `source_dataset_name`
- `media_source_name`
- `curated_sample`
- `source_dataset_id`
- `media_source_id`
- `topic_id`
- `topic_name`
- `topic_region`
- `country`
- `is_conflict_related`
- `categories`
- `tags`

## 维表设计

实际已创建：

- `public.media_source`

结构：

- `id`
- `domain`
- `region_code`
- `created_at`

`news.media_source_id` 已经接到了这个维表。

## 为什么这样取舍

- `abstract`
  - 不是必须
  - 有 `body` 以后，摘要可以后算
  - 对当前主表价值不如 `url_hash`

- `url_hash`
  - 建议保留
  - 它对去重、幂等写入、后续 upsert 很有用
  - 比 `abstract` 更偏底层能力字段

- `language`
  - 不建 `language_id` 维表
  - 直接存短码，如 `en`、`ar`、`ja`

- `region`
  - 不建 `region_id` 维表
  - 直接存简写 code
  - 当前映射：
    - `AF`
    - `AP`
    - `AS`
    - `EU`
    - `GL`
    - `LA`
    - `ME`
    - `NA`
    - `SA`

## 转换脚本

- [prepare_news_table_rows.py](/root/data/globemind/scripts/prepare_news_table_rows.py)

## 入库脚本

- [load_news_to_postgres.py](/root/data/globemind/scripts/load_news_to_postgres.py)

示例：

```bash
python3 scripts/prepare_news_table_rows.py \
  --input data/historical_news/wave1_articles_merged.jsonl \
  --output data/historical_news/news_table_rows.jsonl
```

```bash
python3 scripts/load_news_to_postgres.py \
  --input data/historical_news/news_table_rows.jsonl
```
