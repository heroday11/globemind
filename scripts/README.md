# scripts

## 职责

脚本目录承载新闻发现/抽取/加载、L1/L2/L3 分层处理、ECB+ 映射、信号/来源目录、质量评估和运行时控制等一次性或批处理工具；`ci/` 放导入边界、数据库消费者、feature registry、公开声明和运行时配置检查。

不负责定义核心算法（见 `core_pipeline/`）、提供 API（见 `backend/api/`）或替代受控部署控制器。脚本名含 `load`、`write`、`backfill`、`refresh`、`stream`、`crawl`、`extract`、`train` 的通常会写文件/数据库或调用外部服务。

## 主要入口

- 运行时配置/控制：`globemind_runtime.py`、`db_runtime_config.py`、`runtime_control/`。
- 新闻分层：`run_news_l1_*`、`run_news_l15_segments.py`、`run_news_l2_storylines.py`、`run_news_l3_macro_events.py`。
- 历史数据：`historical_crawl_*`、`extract_historical_articles.py`、`load_news_to_postgres.py`。
- 质量与 CI：`news_ingest_quality.py`、`continuous_audit*.py`、`ci/check_*.py`。

## 依赖与环境

使用后端/根目录 Python 环境及其 requirements；部分脚本需要 PostgreSQL、Milvus、vLLM、代理池、网络或 GPU。配置必须来自环境变量/受控 secret-file，不能从命令行或日志暴露密码、URL、token。脚本的 `--help` 可用于确认参数，实际执行前须核对输入、输出、checkpoint 和权限。

## 开发与测试

只做静态/纯测试时可运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest backend/tests -m "not live_db and not integration and not gpu"
```

CI 检查脚本可在隔离工作树中逐个执行；不要以批量 shell 命令启动抓取、LLM、流式加载或持续审计。任何会写数据库、覆盖输入、访问网络或长时间运行的脚本，都应使用专门的作业清单、checkpoint、回滚和运维批准。

## 数据与安全边界

脚本可能读写 `data/`、工作目录、日志、代理池和 PostgreSQL/Milvus。生产数据与凭据不应复制进 fixture 或日志；输入输出路径必须显式且位于受控目录，不能指向 release 目录。禁止使用脚本绕过数据库角色、TLS、鉴权或运行时控制。

## 当前状态

目录同时包含稳定的 CI/审计检查、实验性评估和历史迁移脚本；没有统一安全的“一键运行全部脚本”入口。以每个脚本的参数、调用方和对应 deploy 控制器为准。

根目录过去保留的 `db_explore*.py`、事件边抽样脚本和全量 L1 shell 是一次性数据库实验：它们带有固定环境假设、会输出业务数据，其中全量脚本还会清空并重写核心表，因此已从当前源码树移除。旧 Dynamic Workflow 和包含同类破坏性逻辑的 Python 编排器也已退出当前源码路径，仅保留脱敏后的历史设计与报告。数据库结构参考使用 `docs/DB_SCHEMA_GLOBEMIND.md`；分类器实验使用 `scripts/train_event_classifier.py`；任何重建任务必须另行建立 checkpoint、回放证明和回滚步骤。
