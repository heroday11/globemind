# GlobeMind V1.0 续接检查点

记录时间：2026-07-11 00:29:17 Asia/Shanghai

适用仓库：`/root/data/globemind`

本文件是当前会话结束前的最新增量事实源。完整的生产基线、发布流程、运行时说明和 V0.10 到 V1.0 总路线仍见：

- `AGENTS.md`
- `docs/operations/HANDOFF_20260711_V1.md`
- `docs/operations/V010_ACCEPTANCE.md`
- `docs/operations/RELEASES.md`
- `docs/operations/RUNTIME_CONTROL.md`
- `docs/architecture/module-map.md`

新会话必须先阅读本文件，再按第 9 节做只读复核。PID、进度、健康状态和工作区内容都可能在记录后变化，不能把本文件中的瞬时值直接当作新的运行事实。

## 1. 用户目标与当前停止点

用户要求继续把 GlobeMind 推进到 V1.0，并达到以下管理目标：

- 前后端按页面或业务功能形成高内聚、低耦合模块。
- 路由页面只做组合，API、DTO、状态、领域规则和私有组件归入 feature。
- 跨 feature 只依赖稳定公共入口，使用自动门禁防止深层导入和反向依赖。
- 所有常驻服务和持续管线有唯一清单、强进程身份、健康证据、检查点、审计和安全生命周期控制。
- 每个版本经过聚焦测试、完整质量门禁、候选环境、生产 smoke 和回滚验证。
- 使用代理团队处理具体且互不重叠的任务，主代理负责规划、集成和验收，同时控制额度消耗。

本次停止点：暂停继续扩改，保存 V0.10 进行中的源码和验证事实，供新会话续接。没有将 V0.10 标记为完成，也没有构建或部署 V0.10。

## 2. 总体状态

| 项目 | 状态 | 事实 |
| --- | --- | --- |
| 生产版本 | 稳定基线 | V0.9.3，最后一次已记录 readiness 健康 |
| 源码版本 | 开发中 | `VERSION=0.10.0` |
| Git | 大量未提交改动 | `main`，HEAD `4c1cdb50064eb119e4189d403154d0611c33b14e` |
| V0.10 发布 | 未开始 | 没有当前源码对应的 candidate/release/生产切换 |
| V0.10 完整门禁 | 未通过验收 | 尚未对当前最终快照运行完整质量门禁 |
| V0.11 | 未开始 | 仅有路线，没有版本级实现验收 |
| V1.0 | 未开始 | 仅有目标和退出标准 |
| 持久 goal | 当前未发现 | 本次检查 `get_goal` 返回空；新会话需重新设置到 V1.0 的 goal |

生产 V0.9.3 位于不可变 release，当前脏源码不会自动影响生产。不要把源码版本 `0.10.0` 解释为线上已经升级。

工作区混合了用户原有改动、V0.9.x/V0.10 工程改造、运行证据、生成数据和实验产物。禁止为了清理工作区执行 `git reset --hard`、`git checkout --` 或批量删除。新会话必须基于现状继续，并在发布前单独整理可审计发布输入。

## 3. 已完成的基础批次

以下内容在本次检查点之前已经落地，详细证据见主交接文档：

- V0.9.1：安全配置、secret 边界和基础质量门禁。
- V0.9.2：schema-v3 可验证发布、固定 Python 运行时、数据库角色和回滚基线。
- V0.9.3：Wave1 受控 loader、四 worker、Cloudflare canonical connector 和生产切换。
- 统一运行时清单：`ops/runtime/services.json` 登记 12 个服务/管线；CLI 当前以只读观察和诊断为主。
- 后端 Story Graph feature、Web 数据库 engine/session 收敛、连接池预算下降、fork 后 dispose、identity security 原语下沉。
- 前端 Ground News 和 Story Graph 第一层 feature 化及 Node 契约测试。
- 架构门禁支持后端/前端 feature 公共入口和历史债务 ratchet。

## 4. 本轮新增进度

### 4.1 Data Assistant 第一层 feature 化：实现完成，待全局验收

主要结果：

- 原 `src/views/DataAssistant.vue` 变成 24 行兼容路由壳，不增加 DOM 包装，保留 `embedded`、`pageSkill` 和 `page-action` 契约。
- 主体验迁入 `src/features/assistant/AssistantExperience.vue`，从原 5240 行降至 4933 行。
- `AssistantDrawer.vue` 从 shared 候选区迁入 assistant feature，关闭 `components -> views/DataAssistant` 反向依赖。
- 原 `views/DataAssistant/` 私有目录迁到 `features/assistant/components/`。
- 7 个 Drawer 调用页面和路由壳统一通过 `@/features/assistant/index.js` 公共入口导入。
- `api.js` 集中 schedule、workspace/file、session/message、KB、sites/members 和流式助手请求。
- `dto.js` 集中 provider、workspace、session、message、schedule DTO。
- `state.js` 集中 SSE 分帧、取消作用域、错误判定、格式化和 page-action 查询提取。
- assistant Vue 组件不再直接调用 `fetch`；请求取消和 HTTP 错误映射已统一。

关键文件：

```text
frontend/vue_project/src/features/assistant/
  AssistantExperience.vue
  AssistantDrawer.vue
  api.js
  dto.js
  state.js
  index.js
  components/*
frontend/vue_project/src/views/DataAssistant.vue
frontend/vue_project/tests/assistant-feature.test.mjs
frontend/vue_project/docs/v0.10-assistant-module-audit.md
```

已验证：

```text
npm run test:features: 16/16 passed
assistant 聚焦 ESLint: passed
assistant 依赖方向/旧路径/直接 fetch 检查: passed
git diff --check: passed
隔离 main-only 生产构建: passed, 4183 modules
```

已知残余：

- `AssistantExperience.vue` 仍有 4933 行，后续应按 chat、reports、briefings、workspace 提取 composable/reducer/repository。
- 私有样式仍有 7824 行，拆分前需要桌面和嵌入式视觉回归基线。
- 当前没有 Vue 组件级测试框架，复杂交互尚缺浏览器回归。
- 完整前端 ESLint 仍有 29 个 assistant 范围外历史错误；不能误报全量 lint 已通过。
- 构建仍有两个绝对路径资源和 `vendor-echarts` 大包的既有警告。

### 4.2 V11 搜索 current-table 适配：约 95%，专项通过

主要结果：

- 新增 `backend/api/features/search/{__init__.py,v11.py}`。
- `/api/dashboard/search/v11-clusters*` 已映射到当前 L3/L2/L1 表。
- 支持 macro/micro/cluster 与 l3/l2/l1 兼容层级标签。
- SQL 全部参数化；空搜索、非法 ID/层级零查询；未知但合法 ID 返回空页。
- 路由通过 feature 公共入口使用；`search_service.py` 的 V11 旧实现变成薄转发。
- 搜索路径中的缺失 legacy relation 引用已移除；旧 cluster tree/event-coref 展示名改用当前 L2/L1 表。

改动文件：

```text
backend/api/features/search/__init__.py
backend/api/features/search/v11.py
backend/api/routes/search.py
backend/api/services/search_service.py
backend/tests/test_v11_search_feature.py
```

验证结果：

```text
V11 专项测试: 约 22 项通过
搜索 + Web/DB/架构/身份组合测试: 53/53 passed
Ruff E4/E7/E9/F/I: passed
测试环境 import smoke: passed
完整 backend/tests: 647 passed, 2 failed
```

完整后端测试的两个失败当前判断与 V11 改动无关，但必须由新会话复核后才能关闭：

1. `test_opinion_api.py::test_db_hardcoded_password_flagged`：旧断言要求 `api/core/db.py` 含 `os.getenv`，与已完成的环境集中化方向冲突。
2. `test_runtime_inventory_contract.py::test_legacy_loop_inventory_does_not_claim_strong_identity_before_takeover`：与共享 runtime inventory 中 `news_quality_labels` 的 loop meta 状态有关。

不要重复实现 V11 适配，也不要用恢复旧查询或创建占位 legacy 表的方式处理测试。

### 4.3 Managed loop controller 加固：代码与聚焦验证完成

补强内容：

- start ticks 不一致时先做 workload 三态识别：目标 loop 或不可读证据为 `unverified`，明确无关进程才是 `stale`。
- 目标 loop 的 meta 即使被篡改，也不能被清理或导致第二个重复管线启动。
- 只有 `/proc/<pid>/stat` 不存在，或当前 start ticks 与记录不同时，才可将 recorded/fresh identity 视为死亡。
- `stat` 存在但读取/解析失败时保留 PID/meta 并失败关闭。
- signal helper 的失败码由 stop 统一映射，移除重复退出语义。

验证结果：

```text
controller/治理测试: 93 passed
相关 pipeline 测试: 2 passed
bash -n、Ruff、format、diff/空白检查: passed
shellcheck: 未安装，未执行
```

本轮没有调用生产 controller，没有修改生产 PID/meta，没有发送生产信号，也没有连接数据库。`daily_ingest` 和 `quality_labels` 仍不能因为代码存在就视为已完成线上接管；维护窗口、检查点、隔离演练和回滚证据仍是前置条件。

## 5. 当前门禁事实

2026-07-11 00:27 左右只读执行 import boundary：

```text
status: baseline_update_required
new_debt: 0
resolved_debt: 1
resolved item: frontend/vue_project/src/components/AssistantDrawer.vue
current historical debt: 164
  backend direct environment reads: 145
  core_pipeline -> scripts: 1
  frontend shared -> views: 18
```

这不是新增架构回归。下一会话应使用门禁提供的安全降基线流程固定已解决的 1 条债务，禁止手工调高 baseline。

尚未完成的版本级验证：

- 当前最终快照的完整 `deploy/run_quality_gate.sh`。
- 两个完整后端测试失败的根因确认和兼容修复。
- 关键页面 Playwright/浏览器 smoke 与视觉回归。
- V0.10 固定 Python runtime、schema-v3 release 和独立 candidate。
- 四 worker replacement、代表 API、Cloudflare 交接和 V0.9.3 回滚演练。

因此 V0.10 不能标记完成，不能直接切生产。

## 6. 风险与禁止事项

- 不要回滚或覆盖当前未提交的 Data Assistant、V11、runtime controller 和其他用户改动。
- 不要对 PID-only 记录发信号，不要使用宽泛 `pkill`。
- 不要在没有检查点、重放证明、回滚步骤和维护窗口时接管长管线。
- 不要打印进程完整环境、数据库 URL、token 或 secret 内容。
- 不要导入、修改、清缓存或修补 `current`、`previous` 或版本化 release。
- 不要创建缺失旧表的占位表，也不要扩大数据库权限来掩盖旧 API 依赖。
- 不要把 runtime 的 `degraded` 直接理解为服务宕机；先区分本地健康和外部依赖未验证。
- 当前工作区包含运行 PID/meta 和生成数据。任何清理必须先区分源码缓存、审计证据和活跃运行状态。

## 7. 后续版本路线

### V0.10：关闭当前边界与发布基线

1. 只读复核生产、工作区和当前测试状态。
2. 处理 import-boundary 已解决基线，保持零新增债务。
3. 复核 V11 的 2 个全套测试失败；更新过时测试契约或隔离 runtime fixture，不倒退环境集中化和强身份设计。
4. 集成审查 Data Assistant、V11 和 managed loop controller，运行聚焦测试及完整质量门禁。
5. 补最小浏览器 smoke：登录、Ground News、Story Graph、Data Assistant、V11 搜索、Pipeline Monitor。
6. 整理可审计发布输入，建立明确提交边界，不携带日志、PID、实验数据或缓存。
7. 构建固定 runtime、schema-v3 V0.10 release 和独立 candidate。
8. 完成四 worker、worker replacement、代表 API/页面、Cloudflare、回滚 artifact 验证后才决定生产切换。

V0.10 退出标准：完整门禁通过，V11 不依赖缺失表，三个前端 feature 公共入口稳定，统一 runtime 可可信观察，生产升级与 V0.9.3 回滚均有证据。

### V0.11：扩大模块化和受控管线接管

- Data Assistant 第二层拆分 chat、reports、briefings、workspace。
- 前端继续拆 Data Search、Pipeline Monitor、Ground News Home、sentiment 等大页面。
- 后端形成 assistant、search、operations、financial、opinion 的 facade/contracts/application/repository 边界。
- 环境读取收敛到类型化配置装配层，持续下调 145 条历史债务。
- 建立核心页面/API 契约测试和浏览器 smoke。
- 对 daily ingest、quality labels 及选定管线逐个完成检查点、重放、隔离 stop/start 和回滚演练后，再开放统一生命周期动作。
- 清理代理池陈旧身份记录，补 PostgreSQL、Cloudflare、模型、代理和关键数据源探针。

V0.11 退出标准：主要页面和后端域可独立理解、测试和升级；通过接管门禁的管线可安全、审计式控制。

### V1.0：形成生产管理基线

- 服务、管线、发布、配置、secret 引用、健康和操作审计都有唯一事实源。
- 所有关键进程具备强身份，控制动作默认失败关闭。
- 核心 feature 有 owner、公共契约、依赖方向、测试入口、健康信号和回滚说明。
- 完成候选发布、worker/tunnel 故障、管线检查点恢复和 V0.11 回滚演练。
- 完成安全、连接容量、性能、可观测性和数据一致性验收，无未关闭高严重度生产安全问题。
- 固化新模块模板、版本发布清单、事故响应和日常运维手册。

达到这些门槛后，项目管理和升级会有质的提升，但后续功能仍必须遵守公共入口、测试、健康和回滚门禁。

## 8. 建议的新会话代理分工

同一时间最多三个边界清晰、文件范围不重叠的子任务：

1. 后端验收：复核 V11 适配和完整后端测试的两个失败，不重做实现。
2. 前端验收：审查 Data Assistant feature、运行浏览器 smoke，并形成下一层拆分设计，不立即同时拆多个大页面。
3. 运行时验收：复核 managed loop/controller/manifest 契约，修复 fixture 或状态判断问题；禁止操作生产生命周期。

主代理负责 goal/plan、import baseline、交叉改动审查、完整门禁、发布输入整理和所有生产决策。

## 9. 新会话第一步

```bash
cd /root/data/globemind

sed -n '1,260p' AGENTS.md
sed -n '1,360p' docs/operations/HANDOFF_20260711_0029_V010_CHECKPOINT.md
sed -n '1,360p' docs/operations/HANDOFF_20260711_V1.md

git status --short
git branch --show-current
git rev-parse HEAD
tr -d '\n' < VERSION

curl -fsS --max-time 15 https://globemind.top/api/health/ready
readlink -f /root/data/releases/globemind/current
readlink -f /root/data/releases/globemind/previous

PYTHONDONTWRITEBYTECODE=1 \
  /root/data/python-runtimes/globemind-web/0.9.3/bin/python -B \
  scripts/globemind_runtime.py status --json

PYTHONDONTWRITEBYTECODE=1 \
  /root/data/python-runtimes/globemind-web/0.9.3/bin/python -B \
  scripts/globemind_runtime.py doctor wave1_loader --json
```

之后重新设置“保持生产安全和管线连续性，完成 V0.10、V0.11 并达到本文件 V1.0 验收标准”的 goal，并建立分阶段 plan。不要仅因为 feature 专项测试通过就跳到发布。

## 10. 可粘贴给新会话的提示词

```text
继续推进 /root/data/globemind 到 V1.0。先完整阅读 /root/data/globemind/AGENTS.md、docs/operations/HANDOFF_20260711_0029_V010_CHECKPOINT.md 和其中引用的 HANDOFF_20260711_V1.md，把 0029 检查点当作最新事实源。

先只读复核生产仍为 V0.9.3、current/previous、runtime status、Wave1 doctor、git status、HEAD 和 VERSION。当前源码是未发布的 V0.10.0，工作区很脏，禁止 reset/checkout/批量清理，禁止从 release 导入 Python，禁止操作 PID-only 进程或启动重复管线。

设置一个持续到 V1.0 的 goal 和 V0.10/V0.11/V1.0 分阶段 plan。V0.10 从现有进度续接，不要重做 Data Assistant、V11 搜索或 managed loop controller：先审查现有改动，安全下调已解决的 import baseline，复核完整后端测试的两个无关失败，运行完整质量门禁和浏览器 smoke。门禁通过后再整理发布输入、构建固定 runtime/schema-v3 candidate，并完成四 worker、API/页面、Cloudflare 与回滚验证。没有候选证据不得切生产。

可使用代理团队，但任务必须具体、互不重叠且独立验收；主代理保留集成、完整门禁和全部生产操作权，并控制额度消耗。V0.10 完成后继续按交接中的 V0.11 和 V1.0 路线推进，不要在中间版本完成后误报总 goal 完成。
```
