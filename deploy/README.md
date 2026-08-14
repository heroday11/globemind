# deploy

状态：current controlled operations navigation
适用范围：只读质量门、候选构建、发布验证和受控运行工具

## 职责

受控运维工具集：管理 Web/vLLM、抓取/抽取/加载循环、数据库角色策略、前端构建、质量门、release 验证和发布事务。它不是普通开发目录，也不是替代 runbook 的一键启动入口。

## 主要入口

- 质量与静态门：`run_quality_gate.sh`、`check_frontend_*.mjs`、`verify_release.py`。
- 发布构建/校验：`create_release.sh`、`build_frontend_release.sh`、`build_python_runtime.sh`；涉及 release/runtime 目录，必须由授权运维执行。
- 服务控制：`start_web_prod.sh`、`vllm_service_ctl.sh`、各 `*_ctl.sh`；它们可能启动、停止或重启长驻服务/作业。
- 数据库策略：`db_runtime_roles.py`、`db_role_policy.py`；`verify` 与 `apply` 权限不同，变更须单独审批。

## 依赖与环境

脚本依赖锁定 Python runtime、Node/npm、数据库/模型服务、secret-file、受控日志和运行时目录。启动/发布前必须核对身份、工作目录、PID 元数据、checkpoint、健康检查、回滚和维护窗口；不要从 release 目录导入或运行 Python。

## 开发与测试

安全的静态检查示例：

```bash
bash -n deploy/run_quality_gate.sh deploy/build_frontend_release.sh
node --check deploy/check_frontend_budgets.mjs
PYTHONDONTWRITEBYTECODE=1 python -B deploy/verify_release.py --help
```

完整质量门和任何 `start|stop|restart|build|promote|apply` 操作都应由运维 runbook 在隔离/候选环境执行。不要在开发机用控制脚本启动真实抓取、流式加载、vLLM 或生产 Web，也不要将 release 目录作为临时工作区。

日常/CI 质量门只校验 `ops/release/content-bundles.json` 的策略结构，因为大型
`expert-skills` 内容包由仓库外制品提供，干净 clone 不包含其被忽略的文件。正式
`stage-content-bundles` 与发布流程仍会校验完整目录摘要、证据文件和秘密扫描；缺少或
漂移的外部内容会阻断发布，策略校验不能替代发布校验。

## 数据与安全边界

部署脚本会接触 release/runtime、数据库、日志、PID/socket、模型和密钥文件；运行状态必须位于受控外部目录。禁止在命令行、日志或 README 暴露 secret/连接串，禁止仅凭 PID 文件杀进程，禁止未经 checkpoint、回放证明和回滚步骤迁移或重启管线。release 目录应只由验证和原子晋升流程管理。

## 当前状态

脚本覆盖候选构建、Web/vLLM、新闻作业和质量验证，但职责边界严格依赖部署 runbook 与环境变量。它是受控运维层而非普通开发层；若只需检查代码，优先使用 shell/node/Python 的只读语法或帮助命令。

## 源码工作区路径迁移

当前脚本默认路径保持不变。未来将日志和运行状态迁移到仓库外的
`GLOBEMIND_RUNTIME_ROOT`，但本次治理不激活该迁移；兼容性契约和必要审批见
[`quality/runtime-path-policy.json`](../quality/runtime-path-policy.json)。任何激活都必须
经过 checkpoint/replay 证明、迁移演练、回滚路径和明确维护窗口，不得由文档或 CI 自动
触发服务重启。
