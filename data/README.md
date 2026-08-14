# data

## 职责

集中存放算法评估/分析结果、ECB+ 参考语料、历史新闻作业输入输出、来源目录、模型产物、代理池运行数据及本地 Milvus 等数据资产。它是数据与作业存储区，不是 Python 包、数据库 schema 或前端静态资源目录。

## 主要入口

- 参考/fixture：`ecbplus/`、`ecbplus_google/` 及明确标注的测试样本。
- 分析结果：`analysis/`、`l1_audit_iter1/` 等；模型和来源资产分别位于 `models/`、`source_curation/`。
- 历史作业和运行状态：`historical_news/`、`runtime/`、`proxy_pool/`；这些由 `scripts/`/`deploy/` 的具体作业约定，不应手工猜路径。

## 依赖与环境

数据由 `core_pipeline/`、`scripts/`、PostgreSQL/Milvus 和模型服务消费；格式以调用脚本参数和现有 manifest/checkpoint 为准。不要为查看数据加载生产 Python release，不要将本目录加入不受控的 `PYTHONPATH`。

## 开发与测试

数据 fixture/参考文件可供隔离测试使用；测试命令：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest backend/tests -m "not live_db and not integration and not gpu"
```

不会提供全目录导入、重建索引或回填快捷命令；这类操作可能覆盖文件、访问网络或写库，必须按作业 runbook 执行。

## 数据与安全边界

只有明确的 fixtures/reference 适合考虑跟踪；历史抓取、运行日志、代理池、checkpoint、数据库转储、模型和本地向量库通常是生成/环境数据，应按仓库忽略规则和数据保留策略处理。禁止提交 secret、个人数据、生产导出或可复用的数据库凭据；不要删除或覆盖未知数据目录。

## 当前状态

目录混合了已跟踪 ECB+ 参考资料、分析快照和现有运行产物；不能假设所有内容都可重建、可公开或可提交。新增数据前应先确认用途、来源、许可、敏感级别和生命周期。
