# Source P0 P1 Report

日期：2026-06-21

- 主库候选总数：`87`
- 输出文件：[p01_master_sources.csv](/root/data/globemind/data/source_curation/p01_master_sources.csv)

## 优先级

- `P0`：`46`
- `P1`：`41`

## 批次建议

- `batch_1_official_and_state`：`5`
- `batch_2_asia_middle_east`：`17`
- `batch_3_global_and_western`：`24`
- `batch_4_public_and_wire`：`5`
- `batch_5_asia_expansion`：`23`
- `batch_6_global_expansion`：`13`

## 类型分布

- `business_media`：`2`
- `global_major_media`：`8`
- `national_major_media`：`52`
- `official_government`：`2`
- `official_io`：`2`
- `public_broadcaster`：`7`
- `regional_major_media`：`4`
- `state_media`：`4`
- `wire_service`：`6`

## 调整过的抓取入口

- `abcnews_go_com`：`http://abcnews.go.com/alerts/climatechange/` -> `https://abcnews.go.com/international`
- `csmonitor_com`：`http://www.csmonitor.com/The-Culture/Faith-Religion` -> `https://www.csmonitor.com/World`
- `dpa_com`：`https://www.dpa.com/en/about-dpa/ownership-management` -> `https://www.dpa.com/en/international-news`
- `efe_com`：`https://www.efe.com/efe/espana/1/inicio-america/` -> `https://efe.com/english/`
- `id_mofcom_gov_cn`：`https://id.mofcom.gov.cn/mytz/index.html` -> `https://fdi.mofcom.gov.cn/EN/come-zonghe-list.html`
- `nbcnews_com`：`https://www.nbcnews.com/science` -> `https://www.nbcnews.com/world`
- `nikkei_com`：`https://www.nikkei.com/business/column/` -> `https://www.nikkei.com/world/`
- `thestar_com_my`：`http://www.thestar.com.my/education/news` -> `https://www.thestar.com.my/news/world`
- `todayonline_com`：`http://www.todayonline.com/watch` -> `https://www.todayonline.com/world`
- `tuoitre_vn`：`http://tuoitre.vn/video/moi-nhat.htm` -> `https://tuoitre.vn/the-gioi.htm`

## 说明

- `preferred_crawl_entry` 是建议优先抓取的入口页。
- `original_url` 保留了你最初给出的原始入口，方便回溯。
- 建议先按 `batch_1` 到 `batch_3` 开抓，再逐步扩展到 `batch_4` 到 `batch_6`。
