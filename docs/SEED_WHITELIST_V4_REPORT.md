# Seed Whitelist V4 Report

日期：2026-06-21

- 输入文件：[political_signal_priority_v4.csv](/root/data/globemind/data/source_curation/political_signal_priority_v4.csv)

## 输出

- [seed_whitelist_priority_v4.csv](/root/data/globemind/data/source_curation/seed_whitelist_priority_v4.csv)
- [seed_whitelist_priority_v4_a.csv](/root/data/globemind/data/source_curation/seed_whitelist_priority_v4_a.csv)
- [seed_whitelist_priority_v4_b.csv](/root/data/globemind/data/source_curation/seed_whitelist_priority_v4_b.csv)

## 统计

- 全部种子：`173`
- `A` 级种子：`104`
- `B` 级种子：`69`

## 规则

- `A`：全部 `official_direct`、全部 `wire_fast`、以及 `P0` 主媒体源。
- `B`：其余 `P1` 主流媒体叙事源。
- 建议抓取顺序：先 `A` 后 `B`。
