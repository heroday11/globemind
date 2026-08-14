# GlobeMind 文档索引

状态：current navigation policy
适用范围：源码仓库文档入口与归档分类
事实源：当前目录结构与各文档自身声明

文档按用途分层。新贡献者先读根目录 [`README.md`](../README.md)、[`CONTRIBUTING.md`](../CONTRIBUTING.md) 和 [`AGENTS.md`](../AGENTS.md)，再按任务进入下面的分类。

文档元数据约定：当前政策/架构文档在开头声明状态、适用范围和事实源；带日期的
handoff、progress、benchmark、discovery 和实验报告属于历史证据。历史内容不批量
改写，若结论变化请新增当前文档并在归档索引中保留原文。

- [`REPOSITORY_GOVERNANCE.md`](REPOSITORY_GOVERNANCE.md) — 数据、脚本、运行路径和文档治理契约。

## Developer guides

- [`development/README.md`](development/README.md) — 新人开发文档入口。
- [`development/LOCAL_DEVELOPMENT.md`](development/LOCAL_DEVELOPMENT.md) — 前端 mock、API 与 pipeline 的安全本地模式。
- [`development/TESTING.md`](development/TESTING.md) — 聚焦测试、marker 与 CI 等价质量门禁。
- [`../backend/api/features/README.md`](../backend/api/features/README.md) — 后端 feature 责任与首选契约测试。
- [`../frontend/vue_project/src/features/README.md`](../frontend/vue_project/src/features/README.md) — Vue feature 责任与首选契约测试。

## Current development

当前开发与能力状态，关注“代码已实现”与“真实数据/候选/生产门禁已验收”的区别：

- [`GLOBEMIND_CONTINUOUS_IMPROVEMENT_MASTER_20260809.md`](GLOBEMIND_CONTINUOUS_IMPROVEMENT_MASTER_20260809.md) — 持续改进总控与边界记录。
- [`V15_V2_V3_AI_IMPLEMENTATION_PROGRESS_20260809.md`](V15_V2_V3_AI_IMPLEMENTATION_PROGRESS_20260809.md) — AI 能力与外部阻断项。
- [`V10_TRUSTED_DATA_FOUNDATION_PROGRESS_20260809.md`](V10_TRUSTED_DATA_FOUNDATION_PROGRESS_20260809.md) — 可信数据基础与证据门禁。
- [`backend/ROADMAP.md`](../backend/ROADMAP.md) — 后端质量优化路线图（计划，不是完成声明）。

## Architecture

- [`architecture/module-map.md`](architecture/module-map.md) — 模块职责与边界。
- [`architecture/feature-registry.md`](architecture/feature-registry.md) — 功能登记与所有权。
- [`DEVELOPMENT_PLAN.md`](DEVELOPMENT_PLAN.md) — 仓库整理与模块化开发路线。
- [`DB_SCHEMA_GLOBEMIND.md`](DB_SCHEMA_GLOBEMIND.md) — 数据库结构参考。
- [`NEWS_TABLE_FIELD_MAPPING.md`](NEWS_TABLE_FIELD_MAPPING.md) — 新闻字段映射。
- [`L1_MAIN_PIPELINE.md`](L1_MAIN_PIPELINE.md) — L1 事件处理背景。

## Operations

生产、运行时和候选环境操作必须先读对应 runbook，并遵守根目录 `AGENTS.md`：

- [`operations/PYTHON_RUNTIME.md`](operations/PYTHON_RUNTIME.md) — 锁定 Python 运行时与诊断规则。
- [`operations/RELEASES.md`](operations/RELEASES.md) — release 校验、提升与回滚边界。
- [`operations/RUNTIME_CONTROL.md`](operations/RUNTIME_CONTROL.md) — 运行控制与安全检查。
- [`operations/RUNTIME_SERVICE_CATALOG.md`](operations/RUNTIME_SERVICE_CATALOG.md) — 服务目录。
- [`operations/DATABASE_RUNTIME_ROLES.md`](operations/DATABASE_RUNTIME_ROLES.md) — 数据库运行时角色。
- [`operations/CONTINUOUS_AUDIT.md`](operations/CONTINUOUS_AUDIT.md) — 持续审计。
- [`operations/FINAL_CLOSEOUT_HANDOFF_20260810.md`](operations/FINAL_CLOSEOUT_HANDOFF_20260810.md) — 收口交接记录。

## Reference

数据来源、事件聚类、检索和产品背景等参考资料包括：

- [`SOURCE_CURATION_GUIDE.md`](SOURCE_CURATION_GUIDE.md) 与 [`MEDIA_SOURCE_PROFILE.md`](MEDIA_SOURCE_PROFILE.md) — 来源整理与媒体画像。
- [`L1_CLUSTERING_SUMMARY.md`](L1_CLUSTERING_SUMMARY.md) 与 [`L1_L15_L2_PIPELINE_20260627.md`](L1_L15_L2_PIPELINE_20260627.md) — 聚类/叙事线研究记录。
- [`HISTORICAL_NEWS_ACQUISITION.md`](HISTORICAL_NEWS_ACQUISITION.md) — 历史新闻获取背景。
- [`涉华舆情指数系统_设计方案.md`](涉华舆情指数系统_设计方案.md) — 涉华指数设计参考。
- [`config/runtime/README.md`](../config/runtime/README.md) — 环境变量目录与变更控制。

## Archive

带日期的 handoff、progress、benchmark、discovery 和 crawl 报告是历史证据：它们记录某个时间点的状态、假设或实验结果，不自动代表今天的运行状态、覆盖率、准确率、许可或生产批准。阅读时必须以文档更新时间、代码现状和当前门禁复核为准。

- [`HISTORICAL_CRAWL_HANDOFF_20260621.md`](HISTORICAL_CRAWL_HANDOFF_20260621.md) — 历史抓取交接证据。
- [`HISTORICAL_CRAWL_JOB_CONTROL.md`](HISTORICAL_CRAWL_JOB_CONTROL.md) — 历史任务控制资料。
- [`archive/README.md`](archive/README.md) — 历史 CLI、产品 QA 和旧工作流索引。
- `计划书/`、来源 discovery/report、benchmark 和带日期的阶段报告 — 研究与实验归档。

归档资料不得被当作当前可执行命令清单；如需复现实验，应先建立隔离环境、确认依赖和数据授权，并由负责人确认不会触及生产边界。
