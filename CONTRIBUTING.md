# 贡献指南

感谢你为 GlobeMind 提交改进。这里的代码同时服务于在线 API、前端、离线研究和数据治理；提交前请说明改动属于哪一层，并保持产品声明与实际证据一致。

## 开始之前

1. 阅读 [`AGENTS.md`](AGENTS.md) 和 [`README.md`](README.md)。
2. 使用 Python 3.11、Node 22 和本地 PostgreSQL；按 README 配置隔离环境。
3. 不要把 `.env`、密码、token、模型密钥、真实数据库导出或个人数据加入提交。
4. 先检查工作树，保留与本任务无关的现有改动；不要用 reset/checkout 覆盖他人或用户资产。

## 修改与提交

- 让每个提交保持单一目的；描述行为变化、数据/配置假设和潜在回滚点。
- 新增 API 或数据字段时，同时更新契约、权限边界、错误/不可计算状态和相关测试。
- 新增模型、来源或外部连接器时，记录版本、许可、覆盖、质量和 provenance；不要把 mock 或一次成功请求写成持续可用证明。
- 不要在没有明确需求时改变数据库 schema、运行时角色、调度器或部署配置。
- 文档中的命令必须在当前仓库中有对应路径和入口；历史命令要明确标记为 archive。
- 代码修改使用精确补丁，避免重排无关文件或提交生成的构建产物、缓存和字节码。

## 本地验证

在仓库根目录运行与改动相关的最小门禁；Python 命令从启动前设置禁止字节码：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -q
npm --prefix frontend/vue_project run lint
npm --prefix frontend/vue_project run test:features
npm --prefix frontend/vue_project run build:main-only
PYTHONDONTWRITEBYTECODE=1 PYTHON_BIN=python deploy/run_quality_gate.sh
```

Python lint 当前按 `deploy/run_quality_gate.sh` 内的受控目标清单渐进收紧；不要把尚未清零的全仓历史 Ruff 结果描述为已通过。测试需要 PostgreSQL、GPU、外部服务或长时间运行时，按 pytest marker 和对应文档准备依赖，并在提交说明中注明未运行项及原因。不要为了通过门禁删除测试、放宽安全检查或跳过数据质量闸门。

## 发布与运行边界

贡献者只在源码仓库或隔离 staging 目录工作。禁止运行或导入任何生产 release、`previous`、`rejected` 或版本化发布目录中的 Python；不要把发布目录加入 `PYTHONPATH`。不要依据 PID 文件猜测进程，也不要未经 checkpoint、回放证明、回滚方案和明确维护步骤停止、重启或迁移服务/长管线。发布流程见 [`docs/operations/RELEASES.md`](docs/operations/RELEASES.md)。

## Pull request 内容

请在说明中包含：

- 问题背景、范围和不在范围内的事项；
- 关键实现与配置/迁移影响；
- 测试命令和结果，或明确列出未运行项；
- 数据、模型、许可、安全和隐私影响；
- 如有 UI 变化，附本地截图或可复核步骤；如有回滚需求，说明回滚点。

维护者可能要求补充人工审阅、候选环境证据或独立安全/无障碍验证。代码测试通过不等于生产发布批准。
