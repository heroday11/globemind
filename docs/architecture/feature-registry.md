# GlobeMind V1 Feature 所有权与公共入口事实源

状态：V1 治理门禁已启用；默认与 release-ready 校验均通过

机器可读事实源：`ops/features/registry.json`

失败关闭校验器：`scripts/ci/check_feature_registry.py`

## 1. 权威边界

`registry.json` 是核心 Web feature 的唯一机器可读事实源，统一回答以下问题：

- 哪个稳定责任角色对 feature 端到端负责；
- 后端 `__init__.py` 与前端 `index.js` 中哪个是唯一公共入口；
- feature 拥有哪些后端 URL 命名空间与前端路由级页面；
- 哪些跨 feature 依赖、契约测试、健康信号、候选 smoke 和回滚机制已有仓库证据；
- 哪些遗留能力尚未建立 facade，因而必须作为 `coverage_gaps` 阻断发布就绪结论。

架构文档用于解释规则，不重复维护另一份 feature 列表。运行服务、进程和管线的事实仍由 `ops/runtime/services.json` 管理；service owner 不能替代 feature owner，feature registry 也不复制运行时 PID、命令或探针。

## 2. V1 清单现状

当前清单登记：

| 事实 | 数量 | 状态 |
| --- | ---: | --- |
| 稳定责任角色 | 10 | 全部被 feature 或 gap 引用 |
| 核心 feature | 18 | 公共边界全部 verified |
| 后端 facade | 18 | 与目录双向一致 |
| 前端 facade | 10 | 与目录双向一致 |
| 后端路由模块 | 20 | 全部被 feature 或 gap 认领 |
| URL 命名空间 | 37 | 无重复或跨 owner 前缀重叠 |
| 路由级页面 | 20 | 路由与组件归属唯一 |
| 显式 feature 依赖 | 19 | 无重复、自依赖或环 |
| coverage gap | 0 | Auth/Identity 与 Dashboard 已建立正式 facade |

Auth/Identity、Dashboard、Data Governance、Evidence Chain、Authoritative Data、Research Workflow、Model Assurance、Service-level Measurement 与 Temporal Entity Governance 已成为正式 feature：请求契约、MFA/会话、Dashboard readiness、数据登记、权威连接器、文章引用、研究项目、模型评测、脱敏服务级观测和人工实体治理均从公共 facade 暴露。Ground News 同时建立后端 health facade。旧 route 中尚未迁完的业务编排仍按迁移地图继续收敛，但不再存在没有 owner 或入口的核心 route module。

`owner_id` 指向 registry 内的稳定责任角色，而不是临时个人姓名。人员安排变化时只调整组织对角色的承接，不应批量改写 feature 身份、入口和依赖关系。新增 owner 定义若没有任何 feature 或 gap 使用，会被视为漂移并拒绝。

## 3. Schema v2

顶层字段职责：

| 字段 | 含义 |
| --- | --- |
| `owners` | 可引用的稳定责任角色目录 |
| `managed_facades` | 固定的后端/前端 facade 根和入口文件名 |
| `features` | 已建立公共模块边界的正式 feature |
| `coverage_gaps` | 有现存代码和 owner、但尚无正式 facade 的范围 |

每个 feature 必须声明：

- `owner_id`：必须引用已登记 owner；
- `public_entries`：至少一个真实 facade，且全仓唯一；
- `routes`：后端命名空间、精确/前缀语义和源文件 locator；
- `pages`：完整前端路由、路由级 Vue 组件和 router locator；
- `dependencies`：只记录仓库内可证明的 feature 依赖；
- `contract_tests`：可直接执行的 pytest 或 Node feature contract；
- `health_signal`、`candidate_smoke`、`rollback`：状态、描述、证据和 blocker；
- `boundary_status`、`blockers`：公共边界是否已经满足门禁。

`boundary_status=verified` 只表示 facade 完整、归属唯一且没有跨 feature 深层导入。它不表示旧 route 中的所有业务代码都已搬入 feature，也不表示该 feature 已能独立部署。当前回滚单元仍是完整、不可变 Web release；清单把这种真实能力登记为 `whole_release`，不宣称支持文件级热替换。

## 4. 失败关闭门禁

默认校验按以下顺序执行，任一项失败均返回非零退出码：

1. 对 JSON 精确 schema、重复 key、ID、owner 引用和状态/blocker 组合做校验。
2. 检查所有文件与单行 locator 确实存在，防止路由、页面、测试或回滚证据静默漂移。
3. 双向比较 `backend/api/features/*/__init__.py` 和 `frontend/vue_project/src/features/*/index.js`；新增未登记 facade、删除入口或伪造深层文件为入口都会失败。
4. 比较 `backend/api/routes/*.py` 与 feature/gap 引用；新增未认领路由模块或移除清单证据都会失败。
5. 拒绝重复 URL 命名空间、跨 owner 前缀重叠、重复页面路由和跨 feature 重复页面组件归属。
6. 检查依赖引用与 DAG，拒绝未知 feature、自依赖、重复边和循环。
7. 复用 `check_import_boundaries.py` 的 AST/源码扫描，拒绝后端或前端从 facade 之外深层导入其他 feature。

校验器只读文件，不导入应用、不连接数据库、不访问网络，也不启动、停止或信号任何服务和管线。

## 5. 两种校验模式

日常质量门禁严格验证所有当前事实，同时允许有 blocker 的 `pending` 或 `blocked` 记录：

```bash
PYTHONDONTWRITEBYTECODE=1 /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B scripts/ci/check_feature_registry.py
PYTHONDONTWRITEBYTECODE=1 /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B scripts/ci/check_feature_registry.py --format json
```

发布就绪模式在相同门禁之上，要求所有 boundary、health、candidate smoke 和 rollback 均为 `verified`，且 `coverage_gaps` 为空：

```bash
PYTHONDONTWRITEBYTECODE=1 /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B scripts/ci/check_feature_registry.py --release-ready
```

当前 `--release-ready` 通过。Assistant、Search、Financial Alerts、Graph Briefing、Story Graph、Ground News、Identity 和 Dashboard 由需要有效登录身份的 `/api/health/features` 提供统一失败关闭信号：数据库 feature 使用当前 Web role 执行真实关系/列读取，文件型 feature 检查配置路径的读写能力与严格 JSON 可读性，Assistant 还检查 scheduler 的实时状态。Service-level 与 Entity Governance 另有认证只读状态契约和离线候选校验；Financial Alerts 的候选检查只读取隐私最小化的 triage 聚合，不执行处置 mutation。公开页面只读取 `/api/status`，该契约仅保留研究者需要的数据截止、延迟、更新时限和脱敏工作流聚合；它不返回依赖、关系、路径、调度器、请求主体或探针延迟，且没有获批目标时固定为 `not_computable`。

`release-ready=true` 表示所有权、入口、证据和健康机制已经闭合，不替代候选运行结果。候选仍必须携带短期 Bearer 身份通过 `/api/health/features`、正式 data catalog、段落级证据、研究存储和 model assurance 等只读强门禁；任一数据记录 blocked、模型评测不合格、认证失败或端点不可达都应阻断，而不是改 registry 状态绕过。

## 6. 原子维护流程

一次 feature 变更必须在同一提交中完成以下适用项：

1. 建立或修改 facade，只从入口导出稳定 contract；
2. 更新 owner、URL 命名空间、页面、依赖和契约测试事实；
3. 为新证据填写真实 locator，为未完成能力保留 blocker；
4. 运行 registry 校验、feature registry 单测和 import boundary 门禁；
5. 在候选环境验证 health/smoke，确认整版回滚目标后再改变对应状态。

建议聚焦命令：

```bash
PYTHONDONTWRITEBYTECODE=1 /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B scripts/ci/check_feature_registry.py --format json
PYTHONDONTWRITEBYTECODE=1 /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B scripts/ci/check_import_boundaries.py --format json
PYTHONDONTWRITEBYTECODE=1 /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B -m pytest -q backend/tests/test_feature_registry.py backend/tests/test_architecture_gates.py
```

删除、重命名或新增 facade、route module、页面路由、测试或证据文件时，未同步更新 registry 会失败。这是预期的管理约束，不应通过放宽扫描根或添加无期限例外处理。
