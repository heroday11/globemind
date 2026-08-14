# Source Full Classification Report

日期：2026-06-21

## 输入

- 原始站点总数：`636`
- 原始清单：[raw_sources.tsv](/root/data/globemind/data/source_curation/raw_sources.tsv)
- 全量分类结果：[full_source_catalog.csv](/root/data/globemind/data/source_curation/full_source_catalog.csv)

## 分类基础

- 人工/已整理覆盖：`153`
- 规则补齐：`476`

## 优先级统计

- `P0`：`46`
- `P1`：`41`
- `P2`：`492`
- `P3`：`18`
- `Drop`：`39`

## 质量统计

- `A`：`46`
- `B`：`41`
- `C`：`510`
- `D`：`39`

## 动作统计

- `primary_crawl`：`46`
- `secondary_crawl`：`41`
- `selective_crawl`：`492`
- `context_only`：`18`
- `drop`：`39`

## 类型统计

- `business_media`：`5`
- `global_major_media`：`8`
- `national_major_media`：`76`
- `noise`：`40`
- `official_government`：`13`
- `official_io`：`2`
- `public_broadcaster`：`11`
- `regional_major_media`：`8`
- `regional_media_candidate`：`430`
- `regional_network`：`2`
- `state_media`：`15`
- `think_tank_context`：`18`
- `wire_service`：`8`

## 说明

- `P0/P1` 可视为新闻主库候选。
- `P2` 主要用于区域、语种、国家视角补充。
- `P3` 不进入新闻主库，只作分析参考。
- `Drop` 为明确不适合进入新闻正文库的站点。
