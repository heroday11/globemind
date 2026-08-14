# Source Curation Guide

这份说明用于把大而杂的站点清单，快速清洗成适合历史新闻回填的高质量白名单。

## 分级规则

- `A`
  官方政府/国际组织，或全球高价值主流媒体。优先纳入三年历史库。
- `B`
  全国性主流媒体，覆盖有价值，但稳定性和国际影响力略低于 `A`。
- `C`
  地方媒体、智库、评论站、区域站。只在需要补语言或地区覆盖时保留。
- `D`
  非新闻站、论坛、博客平台、词典、企业站、体育娱乐、电商旅游等，直接剔除。

## 默认策略

- 历史正文主库：只保留 `A` 和 `B`
- 分析参考库：按需补 `C`
- 永久排除：`D`

## 当前产物

- [seed_whitelist_high_value.csv](/root/data/globemind/data/source_curation/seed_whitelist_high_value.csv)
  从你给的混合名单里先挑出的一批高价值种子源。
- [official_sources.csv](/root/data/globemind/data/source_curation/official_sources.csv)
  官方政府与国际组织源。
- [global_major_media.csv](/root/data/globemind/data/source_curation/global_major_media.csv)
  可直接进入第一轮历史抓取的主流媒体源。
- [asia_priority_media.csv](/root/data/globemind/data/source_curation/asia_priority_media.csv)
  亚洲重点媒体子集，适合优先补地缘政治与涉华覆盖。
- [excluded_noise_examples.csv](/root/data/globemind/data/source_curation/excluded_noise_examples.csv)
  当前明确应排除的噪声源样例。
- [curate_source_catalog.py](/root/data/globemind/scripts/curate_source_catalog.py)
  用于对更大规模站点表做批量分级。
- [export_source_groups.py](/root/data/globemind/scripts/export_source_groups.py)
  用于把高价值白名单拆成官方源、主流媒体源、亚洲重点源三组。

## 批量清洗

输入文件默认路径：

`data/source_curation/raw_sources.tsv`

要求字段：

- `site_id`
- `url`

执行：

```bash
python scripts/curate_source_catalog.py
```

输出：

- `data/source_curation/curated_sources.csv`
- `data/source_curation/clean_whitelist.csv`

拆分分组：

```bash
python scripts/export_source_groups.py
```

输出：

- `data/source_curation/official_sources.csv`
- `data/source_curation/global_major_media.csv`
- `data/source_curation/asia_priority_media.csv`

## 当前建议

- 第一轮抓取只跑 `A`
- 第二轮按国家和语种缺口补 `B`
- `C` 不进默认抓取队列，否则噪声和清洗成本会迅速上升
