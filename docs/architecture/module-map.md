# GlobeMind 模块边界与迁移地图

状态：V1 公共边界治理已启用；内部迁移持续进行

适用范围：`backend/`、`core_pipeline/`、`scripts/`、`frontend/vue_project/src/`

事实源：`ops/features/registry.json`

门禁：`scripts/ci/check_feature_registry.py`、`scripts/ci/check_import_boundaries.py`

## 1. 本版目标

V1 延续按功能纵向迁移的策略：先用唯一所有权事实源、公共入口、契约测试和依赖门禁固定行为，再逐步把路由、页面中的业务逻辑移入 feature。这样可以单独评估和升级一个模块，同时避免一次性重写复杂页面和生产管线。

`ops/features/registry.json` 统一登记 owner、facade、URL 命名空间、路由级页面、依赖、测试、health/smoke 与回滚。新增 facade 或后端 route module 如果没有同步登记会失败；跨 feature 只能使用 registry 指向的公共入口。模块迁移表只解释方向，不再充当第二份所有权清单。

开发阶段不改变生产进程、数据库结构或正在运行的长管线；任何生产切换都必须经过独立候选和回滚验收。

## 2. 后端目标模型

后端采用 `composition -> feature -> domain` 与 `feature -> platform` 的依赖方向：

```text
backend/bootstrap (composition root)
       |                |
       v                v
backend/features/* --> backend/platform/*
       |
       v
backend/domains/*

scripts/* ------------> core_pipeline/*
```

### Platform

`platform` 提供跨功能的技术能力：配置、数据库连接、认证原语、日志与指标、文件存储、HTTP/AI 客户端。它不能导入具体业务功能。

目标目录：

```text
backend/platform/
  config/
  database/
  identity/
  observability/
  storage/
  ai_clients/
```

约束：

- `platform` 只能依赖标准库、第三方库和自身公开模块。
- `platform` 不得导入 `features`、旧 `api.routes` 或旧 `api.services`。
- 环境变量只能在 `platform/config` 的配置装配层读取；其他模块接收类型化配置。
- 连接池、客户端和文件系统根目录由 composition root 创建并注入，不在业务模块导入时创建。

### Feature

`feature` 是可独立升级、测试和发布评估的纵向功能单元。一个功能包含 API 适配器、用例、端口和基础设施适配器，但不暴露内部实现给其他功能。

目标目录：

```text
backend/features/<feature>/
  api.py
  application.py
  contracts.py
  infrastructure/
  tests/
```

约束：

- `api.py` 只做协议转换、鉴权依赖、输入校验和输出映射。
- `application.py` 编排用例，不读取环境、不创建连接、不依赖 FastAPI 请求对象。
- 跨 feature 调用只通过对方的 `contracts.py` 或显式公共入口。
- feature 可以依赖 `domain` 和 platform 公共接口，不能导入其他 feature 的内部文件。
- composition root 负责把 platform 实现注入 feature，不使用全局隐式单例传递依赖。

### Domain

`domain` 保存稳定的业务规则、值对象和状态转换，不承担 HTTP、SQL、文件或模型调用。

目标目录：

```text
backend/domains/<domain>/
  entities.py
  policies.py
  events.py
  ports.py
```

约束：

- domain 不依赖 FastAPI、SQLAlchemy、文件系统、网络客户端或环境变量。
- `ports.py` 定义业务需要的协议；适配器放在对应 feature 的 `infrastructure/`。
- domain 之间若需共享稳定概念，放入小型 `backend/domains/shared_kernel/`，禁止形成通用工具仓库。

### Composition Root

应用入口是唯一允许同时了解 platform 实现和所有 feature 注册信息的层。它负责加载配置、构造资源、注册路由、启动后台任务及关闭资源。业务代码不得反向导入入口。

## 3. 当前后端迁移地图

| 现有代码 | 目标边界 | 迁移说明 | 优先级 |
| --- | --- | --- | --- |
| `backend/api/core/environment.py`、`runtime_security.py` | `platform/config` | 合并为类型化设置与生产校验；保留单一环境读取入口 | P0 |
| `backend/api/core/db.py`、`db_pool.py`、`orm/` | `platform/database` + feature repositories | 连接与事务留在 platform；业务查询下沉到各 feature repository | P0 |
| `backend/api/services/auth.py`、`routes/auth.py` | `features/identity` | 请求 contract、登录 application/repository 与 identity health 已迁移；资料、收藏和密码流程继续逐用例下沉 | P0 |
| `routes/assistant*.py`、`services/assistant_*`、`hermes_assistant.py`、`file_store.py`、`report_export.py` | `features/assistant` | schedule/config/application 与定时报告 citation-boundary assurance 已迁移；chat、workspace、report 继续按子用例拆分 | P0 |
| `routes/search.py`、`services/search_service.py`、`news_search_v2.py` | `features/search` | contracts/application/v11 facade 已建立；旧 adapter 通过注入保持兼容 | P1 |
| `routes/research_workflow.py`、`features/research_workflow/` | `features/research_workflow` | 项目 ACL、证据/检索快照、审阅、批准、版本清单与差异均由独立 facade 暴露 | P1 |
| `routes/model_assurance.py`、`features/model_assurance/` | `features/model_assurance` | 服务器重算指标、append-only 评测链、漂移与回滚门禁由独立 facade 暴露 | P1 |
| `routes/entity_governance.py`、`features/entity_governance/` | `features/entity_governance` | Search 种子、证据验真、时态实体/关系投影与人工决策由 HMAC 追加链约束；未审种子不进入批准投影 | P1 |
| `routes/authoritative_data.py`、`routes/data_governance.py` | `features/authoritative_data` + `features/data_governance` | 有界连接器与正式数据/模型登记分离；配置或单次请求不等同正式可用 | P1 |
| `routes/evidence_ledger.py` | `features/evidence` | 正文快照、修订和下游判断影响使用 release 外 append-only ledger | P1 |
| `routes/story_graph.py`、`briefing.py` | `features/story_graph` | 复用 search 公共 contract，不直接导入其实现 | P1 |
| `routes/dashboard.py` | `features/dashboard` + `platform/health` | Dashboard contract/repository/readiness facade 与统一 feature health 已建立；展示聚合继续下沉 | P1 |
| `routes/financial.py`、`services/financial_terminal.py` | `features/financial` | 告警 contract/application/原子 repository、可信门禁与追加式 triage 状态机已迁移；外部行情 adapter 后续继续收敛 | P1 |
| `routes/opinion.py`、`opinion_v2.py` | `features/opinion` | 趋势 contract/application/repository/analytics/cache 已迁移；其余端点按指标契约逐批迁移 | P1 |
| `routes/ops_monitor.py` | `features/operations` + `platform/observability` | 心跳/history/storage 已迁移；读取指标与 lifecycle 授权继续严格分离 | P0 |
| `routes/service_level.py`、`features/service_level/` | `features/service_level` + `platform/observability` | 固定路由模板只记录工作流、结果、时长和时间；聚合不含请求数据，未获批准目标时不计算合规 | P1 |
| `backend/agentic_rag/` | 独立 AI bounded context | API 通过公开 facade 使用，禁止跨目录导入内部 db/pipeline 实现 | P2 |
| `core_pipeline/` | 可复用 pipeline domain/library | 只包含算法与可复用执行单元，不依赖 `scripts/` | P0 |
| `scripts/` | pipeline entrypoints | 参数解析、作业装配和进程退出码；允许单向调用 `core_pipeline` | P0 |

已知 import boundary 债务已降为 0。baseline 现在是零容忍门禁，不能通过换文件名、深层导入或新增例外转移债务。当前 registry 已对 18 个后端 facade、10 个前端 facade 和 20 个后端 route module 做双向完整性检查；`boundary_status=verified` 表示公共入口约束成立，不表示旧 route adapter 的内部迁移已经结束。

## 4. 前端目标模型

Vue 主应用采用 `app -> features -> shared`：

```text
frontend/vue_project/src/
  app/                       # router、shell、全局 providers
  features/<feature>/
    pages/                   # 路由级页面
    components/              # feature 私有组件
    api/                     # feature 请求与 DTO 映射
    model/                   # store、composable、状态规则
    index.js                 # 唯一公共入口
  shared/
    ui/                      # 无业务语义的基础组件
    api/                     # 统一 HTTP client、错误映射
    lib/                     # 纯函数
    config/                  # 前端运行配置
```

约束：

- `app` 可以导入 feature 公共入口和 shared。
- feature 可以导入 shared；跨 feature 只能导入对方 `index.js` 明确导出的 contract。
- shared 不能导入 feature、`views`、router 或业务 store。
- page 可以组合 feature 私有组件；shared 组件不能懒加载 page。
- API base、认证 header、错误规范和请求取消统一由 `shared/api` 提供。
- 页面私有样式、测试和资源跟随 feature；真正跨功能的设计 token 才进入 shared。
- `frontend/financial-terminal` 是独立构建单元，通过版本化 URL/API contract 集成，不直接读取 Vue 主应用源码。

过渡期间，现有 `src/components`、`src/utils`、`src/config` 按 shared 候选区执行同样规则。因此 `components -> views` 已作为历史债务纳入门禁，不能继续增加。

## 5. 当前前端迁移地图

| 现有代码 | 目标 feature/shared | 迁移说明 | 优先级 |
| --- | --- | --- | --- |
| `router/index.js`、`App.vue`、`components/appNav.vue` | `app/` | router 作为组合层；导航不再预加载具体 `views` 实现 | P0 |
| `config/api.js`、`utils/apiError.js`、`utils/auth.js` | `shared/api`、`shared/config` | 建立单一 HTTP/认证边界 | P0 |
| `components/LoginModal.vue`、登录/注册/找回页面 | `features/identity` | 共享表单与页面状态，移除重复认证逻辑 | P0 |
| `DataAssistant.vue`、`views/DataAssistant/`、`AssistantDrawer.vue` | `features/assistant` | 组件、API/state、briefing、workspace、reports 已迁移；chat transport/reducer 正在收口 | P0 |
| `DataService/data-search.vue` | `features/search` | API、model、request、storage 已迁移，页面不再持有 endpoint | P1 |
| `GroundNews*.vue` | `features/ground-news` | API、presentation 与 Home 规则已迁移；页面保持独立编排 | P1 |
| `StoryGraph*.vue/js/css` | `features/story_graph` | 图渲染 adapter 与查询状态分离 | P1 |
| `PipelineMonitor.vue` | `features/operations` | 心跳、monitor API/model/request/scheduler 已迁移，页面不再持有 token/endpoint | P0 |
| `sentimentAnalysis.vue` | `features/sentiment` | API、DTO、缓存、请求仲裁、展示和趋势模型已迁移；页面不再持有 transport | P1 |
| `FinancialTerminal.vue` | `features/financial` | 只保留独立 React 构建的集成边界和失败状态 | P1 |
| `ResearchWorkspace.vue` | `features/research-workflow` | 页面只组合项目、审阅、manifest、快照引用与确定性 JSON/Markdown/HTML/CSV reviewed-draft 下载公共 contract | P1 |
| `ModelAssurance.vue` | `features/model-assurance` | 共享严格 sanitizer；读取、详情和管理员提交均失败关闭 | P1 |
| `EntityGovernance.vue` | `features/entity-governance` | 认证只读工作台严格核对账本、批准投影、关系和历史；人工 mutation 仍由管理员 API 与证据门禁约束 | P1 |
| `views/user/`、`UserCenter.vue` | `features/account` | 子路由、账户资料和收藏分模块 | P1 |
| `utils/report*` | `features/reports/model` | 这些工具带业务语义，不进入通用 shared/lib | P1 |

## 6. Ratchet 门禁

当前规则：

1. `backend/api/core` 不得新增对 `api.services` 的导入。
2. `core_pipeline` 不得新增对 `scripts` 的导入。
3. 前端 shared 及过渡候选区不得新增对 `views` 的导入。
4. route 不得直接导入 `dotenv`。
5. 除环境装配模块外，后端不得新增 `os.getenv`、`os.environ.get` 或 `os.environ[...]` 读取。
6. 后端 feature 外部只能导入 `api.features.<feature>` 公共入口，禁止深层导入实现文件。
7. 前端 feature 外部只能导入 `features/<feature>/index.*`，禁止跨 feature 深层导入。
8. 所有实际 backend/frontend facade 必须由 feature registry 唯一认领，所有后端 route module 必须由正式 feature 或 coverage gap 认领。
9. URL 命名空间、页面路由和跨 feature 页面组件归属必须唯一；跨 owner 的路由前缀重叠直接失败。

本地执行：

```bash
PYTHONDONTWRITEBYTECODE=1 /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B scripts/ci/check_import_boundaries.py
PYTHONDONTWRITEBYTECODE=1 /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B scripts/ci/check_import_boundaries.py --format json
PYTHONDONTWRITEBYTECODE=1 /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B scripts/ci/check_feature_registry.py --format json
PYTHONDONTWRITEBYTECODE=1 /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B scripts/ci/check_runtime_config_manifest.py
PYTHONDONTWRITEBYTECODE=1 /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B -m pytest -q backend/tests/test_feature_registry.py backend/tests/test_architecture_gates.py
```

`quality/import-boundaries-baseline.json` 是逐规则、逐文件的最大允许数量。同文件增加、移动到新文件或产生新类型违规都会失败。当前数量减少时，门禁会返回 `BASELINE UPDATE REQUIRED`，要求在同一变更中运行 `--write-baseline` 固化更低数值后再通过；因此已清理的债务不能反弹。

截至 V1 边界治理阶段，所有规则的当前 debt 与 baseline 均为 0。后续任何新增违规都会直接失败，不再存在可消费的历史额度。

`--write-baseline` 会读取已有基线并拒绝任何新增债务，只能保持或降低数值。日常开发不得手工调高 baseline 掩盖违规。清理历史债务后，应在同一变更中降低对应数字；baseline 永远不能调高。

## 7. 分批迁移顺序

1. **配置与 composition root**：建立类型化配置，消除 route/service 的直接环境读取。
2. **身份、运维和 assistant 安全边界**：这些模块影响所有页面、后台任务和生产控制，优先解耦。
3. **search/story graph/ground news**：先定义查询 contracts，再按页面迁移，保持 API 兼容。
4. **opinion/financial**：使用契约测试固定指标语义和外部数据源降级行为后迁移。
5. **pipeline library/entrypoint**：把数据库配置和算法依赖从 `scripts` 提升到可复用 library，再统一调度。
6. **删除旧层**：当 `routes/services/views` 的最后一个映射完成且回归通过，才删除兼容入口。

每次迁移只处理一个 feature，并要求：owner 与入口事实同步、契约测试通过、boundary debt 不增长、候选 smoke 通过、健康信号与回滚状态如实登记。目录变化本身不算完成，依赖方向、公共入口和可审计所有权才是验收标准。

当前 Auth/Identity、Dashboard 和 Ground News 都已建立后端公共 facade，registry coverage gap 为 0。需要登录的 `/api/health/features` 对核心在线 feature 执行真实数据库、文件系统或 scheduler 能力检查；公开 `/api/status` 只发布数据新鲜度和研究用途状态。registry 的默认与 `--release-ready` 门禁均通过。

该结论不替代运行验收。V1 候选必须用短期候选身份请求 `/api/health/features` 并要求 HTTP 200、`ready=true`、全部 check 为 `up`；任何 down、认证失败、超时或 schema/权限失败都阻断晋级。后续内部迁移仍按本节顺序推进，不能因为 registry 已闭合就删除尚在使用的兼容 route/service。
