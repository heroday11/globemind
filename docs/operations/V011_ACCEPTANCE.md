# GlobeMind V0.11 验收记录

状态：**生产晋级完成并通过切换后验收；V0.10 保持为明确回滚版本**

源码版本：`0.11.0`

生产基线：`0.10.0-20260710T182025Z`

最终候选：`0.11.0-20260710T223243Z`

## 1. 验收原则

V0.11 的目标是扩大可独立升级的功能边界，并建立统一运行时控制的安全基础。目录拆分本身不算完成；只有依赖方向、输入契约、并发行为、测试入口、候选行为和回滚证据同时成立，才能晋级生产。

本轮遵守以下生产不变量：

- 不从不可变 release 目录导入或修改 Python 代码。
- 候选仅监听 `127.0.0.1:18091`，不接入 Cloudflare。
- 不重启或向 Wave1、quality labels、daily ingest 长管线发送信号。
- 不向 PID-only 进程发送信号，不使用 `pkill`、`killall` 或负 PID。
- 所有统一运行时条目继续保持 `observe-only`；源码中的 lifecycle 能力不等于生产授权。
- 候选验收期间 `current` 与 `previous` 保持 V0.10/V0.9.3，不提前切换。

## 2. 功能与管理边界

| 范围 | V0.11 结果 | 证据 |
| --- | --- | --- |
| 后端 Search | contracts/application/V11 facade 分层，路由只保留 HTTP 适配；L3/L2/L1 查询不再依赖缺失旧表 | Search feature、V11 契约和候选 HTTP 通过 |
| 后端 Operations | 心跳、历史与存储进入 feature；公开心跳不接收或回显完整 URL/查询参数 | Operations 安全、心跳和候选监控页通过 |
| 后端 Financial | 告警契约、用例和原子 JSON repository 分离；跨进程锁、损坏数据失败关闭、有限浮点校验 | Financial 聚焦测试和候选 API 通过 |
| 后端 Assistant | schedule 配置、契约和用例进入 feature；路由不再直接读取环境 | schedule 聚焦测试和候选 readiness 通过 |
| 后端 Opinion | 趋势契约、分析、缓存、查询和 repository 分离；反馈拒绝 NaN/Infinity | Opinion 聚焦测试和候选 API 通过 |
| 后端配置 | Web 运行路径的环境读取收敛到配置装配边界；共享 DB runtime config 消除反向依赖 | import boundary debt 为 0 |
| 前端 Data Search | API、DTO/model、请求仲裁和收藏存储进入 feature；页面不再持有 endpoint 或直接 fetch | feature contract、桌面/移动浏览器通过 |
| 前端 Pipeline Monitor | API、model、请求仲裁和 scheduler 进入 operations feature | feature contract、桌面/移动浏览器通过 |
| 前端 Ground News | 首页选择、主题、来源、倾向与展示规则进入 feature；修复 `latestStories` 未返回问题 | feature contract、桌面/移动浏览器通过 |
| 前端 Assistant | API/state、briefing、workspace、reports、chat transport/reducer 进入独立 feature 边界 | Assistant contracts、桌面/移动浏览器通过 |
| 前端 Sentiment | API、缓存、DTO、展示、请求与趋势规则进入 feature；移除溢出装饰并收敛移动筛选布局 | Sentiment contracts、桌面/移动浏览器通过 |

`ops/runtime/services.json` 继续作为 12 个服务和管线的清单。生命周期动作仍按静态授权失败关闭，执行前要求 PID、start ticks、boot ID、argv、cwd、依赖和审计路径全部匹配；V0.11 发布不会自动接管现有长管线。

## 3. 最终质量门

最终质量报告：`/tmp/globemind-v011-quality-fixed.json`

SHA-256：`e7a08d95bb8f8284602682e1b87ad1ef6e190aa479fc951df11cfbac535c6aed`

源码快照：`39380dc911d3c406e79673d8b2349afe20391222de6ebdcc9a3085cdbff431fb`

| 门禁 | 最终结果 |
| --- | --- |
| 后端测试 | `909 passed`，failures/errors/skips 均为 0 |
| 前端 feature contracts | `76 passed` |
| 前端 ESLint | 0 errors，0 warnings |
| 前端 ratchet | passed |
| import boundary ratchet | passed，新增债务 0 |
| 质量步骤 | 13/13 passed |
| 源码冻结 | source unchanged；14564 files，136631941 bytes |
| 固定 Web runtime | verified；fingerprint `447b8d1c48601fd4bc352bbf1743b6ddaf23ceee2079533f8d12dd66c27c2594` |
| Candidate/Browser 安全边界矩阵 | `128 passed` |
| V0.10 回滚 artifact | 独立复验通过；manifest `ff1ca0715a2f862750c93fcdd4ed2ca8a57cb8ea31bc217a9d3270fb78747cae` |

## 4. 最终发布物

发布目录：`/root/data/releases/globemind/0.11.0-20260710T223243Z`

| 字段 | 值 |
| --- | --- |
| build id | `0.11.0-20260710T223243Z` |
| git SHA | `4c1cdb50064eb119e4189d403154d0611c33b14e` |
| schema | v3 |
| artifact manifest SHA-256 | `be16c010daf81e97b561883eda1e7f930be3f20c5b39d13fde49b4337f2d52cf` |
| artifact | 28778 files，265374123 bytes |
| runtime | `/root/data/python-runtimes/globemind-web/0.11.0` |
| runtime build input | `8aed9d333601b26b9495be621a51e9857025c439d37bc06937ac016358aca546` |
| secret scan | passed |
| independent production verification | passed |

发布目录及文件均已去除写位。工作区仍是显式审计的 dirty release：`release.json` 记录 `dirty_override=true`、15490 个 Git 状态条目和 14068 个未跟踪或 ignored 输入。源码内容、质量报告、runtime 和 artifact 哈希一致，但仓库提交边界仍是 V1.0 必须关闭的管理债务。

## 5. 候选验收

候选使用四个 worker、固定 V0.11 runtime、`web_runtime` 数据库角色 overlay、独立生成资产目录，并显式设置：

```text
ASSISTANT_SCHEDULE_DISABLE=1
ALLOW_RUNTIME_SCHEMA_MUTATIONS=0
```

最终候选 master 为 `127988/643511765`，cwd 指向最终不可变发布，监听只归属 master 与四个直接 spawn worker。候选已按强身份校验停止，PID/meta 已删除，`18091` 已释放。

| 验收项 | 结果 | 证据 SHA-256 |
| --- | --- | --- |
| HTTP pre | `35/35 passed` | `d43ddfc24655bc78bf80c6b5bd9449f378e3be694ffd9e10a559adbeef55c31c` |
| worker replacement | passed | `14380feaa3a468ba74557ce2d9969be120049518c50d8a3a4939fadb52babbc7` |
| HTTP post | `35/35 passed` | `7a1cd88f82c91acfdd1445a444887e94ddbc69acb278a2055b8c198bd7fcd245` |
| browser final | `18/18 passed` | `4bad5bab98fb25f831163f58f8460d63651047cfe5d79794882dfae39cc40c8f` |

证据目录：`/root/data/evidence/globemind/0.11.0-20260710T223243Z/`

浏览器验收覆盖 9 个场景的桌面与 390px 移动视口。所有场景均无 console error、page error、资源错误、未预期 API、明显重叠或横向溢出；最终截图已人工复核。

worker 演练将 `128032/643512150` 替换为 `128745/643521452`，master 身份不变，前后 readiness、精确 build id 和 listener ownership 均通过。当前内核 namespace 对 `pidfd_open` 返回 `ENOSYS`；演练在两次完整身份复核后使用精确 PID `os.kill(SIGTERM)`，没有使用进程组信号、宽泛匹配或 SIGKILL。

## 6. 被拒绝候选

`/root/data/releases/globemind/0.11.0-20260710T212702Z` 永久保留为失败审计证据，不得晋级。它的 HTTP 与 worker 检查通过，但正式浏览器验收为 `13 passed / 5 failed`，暴露了 Ground News 空白、Data Search 移动溢出和 Sentiment 桌面/移动溢出。修复后 production preview 为 `18/18`，随后重新构建最终 release 并从头执行全部候选门禁，没有复用失败发布物。

## 7. 生产与管线不变量

候选前后以下进程 start ticks 完全不变：

| 进程 | PID/start ticks |
| --- | --- |
| production Web V0.10 | `11345/642133641` |
| Cloudflare tunnel | `11903/642146931` |
| Wave1 loader | `95964/640272850` |
| quality labels | `30799/641086875` |
| daily ingest | `53777/618878341` |

候选期间 `current` 保持 V0.10，`previous` 保持 V0.9.3。Wave1 从 `seen=2270385 / inserted=2231524` 推进到候选结束后的 `seen=2279363 / inserted=2240286 / offset=10101788329`，说明候选没有阻断长管线。

## 8. 候选门槛结论

- [x] 后端完整非 live/integration/gpu/slow 测试通过。
- [x] 前端 ESLint、feature contracts、ratchet 和生产构建通过。
- [x] 核心页面桌面与移动浏览器 smoke 通过。
- [x] 固定 Python runtime 构建和 fingerprint 校验通过。
- [x] schema-v3 release 独立校验通过。
- [x] 四 worker、worker replacement、认证失败、代表 API、静态资源和 release identity 通过。
- [x] 候选调度器禁用、运行时 schema mutation 禁用、生成资产隔离。
- [x] V0.10 回滚 artifact 在切换前独立复验。
- [x] 受保护长管线身份不变且 Wave1 检查点持续推进。

## 9. 发布结论

候选结论：**允许晋级生产**。

生产结论：**V0.11 已完成生产晋级，切换后门禁通过**。

## 10. 生产晋级结果

生产没有使用缺少自动回滚的 `restart`。操作顺序为：外部强身份核验 V0.10，单独停止旧 Web，显式选择 V0.11 release/runtime 启动并验收；只有新服务通过后，才在 `.promotion.lock` 下先原子更新 `previous`、再原子更新 `current`。Cloudflare tunnel 和长管线全程未重启。

| 生产字段 | 结果 |
| --- | --- |
| production master | `3832/643635616` |
| workers | `3854`、`3855`、`3856`、`3857`；均为 master 的直接 spawn 子进程 |
| listener | `127.0.0.1:18089`；只归属 master 与四个 worker |
| executable | `/root/data/python-runtimes/globemind-web/0.11.0/bin/python` |
| cwd | `/root/data/releases/globemind/0.11.0-20260710T223243Z` |
| database | up |
| runtime schema mutation | disabled；日志确认执行只读 schema checks |
| assistant scheduler | enabled、healthy、running；leader `3857`，无 last error |
| current | `/root/data/releases/globemind/0.11.0-20260710T223243Z` |
| previous | `/root/data/releases/globemind/0.10.0-20260710T182025Z` |

切换后验收证据：

| 验收项 | 结果 | 证据 SHA-256 |
| --- | --- | --- |
| production HTTP | `35/35 passed` | `53296f23ba34b54b7cdcbaf9d85b1b0eeb579b9d50c09bc074fd8754a447bce3` |
| production browser | `18/18 passed` | `06b1074d6411da3c728757170da8411031462dbcc21edd1c9602ddec711f5884` |
| public readiness | healthy、精确 V0.11 build id | `d9a3313b6d26bf93753082a9390f8dfee3f15a8b415e503fd30f807d4e2c415e` |
| 连续观察 | 5/5 rounds passed | 本地与公网 readiness、scheduler、worker 数、master/tunnel/pipeline start ticks |

证据位于最终 release 对应目录：

```text
/root/data/evidence/globemind/0.11.0-20260710T223243Z/production-http/
/root/data/evidence/globemind/0.11.0-20260710T223243Z/production-browser/
/root/data/evidence/globemind/0.11.0-20260710T223243Z/production-public/
```

切换后 Cloudflare tunnel 仍为 `11903/642146931`，Wave1 为 `95964/640272850`，quality labels 为 `30799/641086875`，daily ingest 为 `53777/618878341`。Wave1 继续推进到 `seen=2281645 / inserted=2242506 / offset=10112894255`。生产日志没有 ERROR、CRITICAL、Traceback 或 Exception。

回滚目标为 `previous` 指向的 V0.10 release 与 `/root/data/python-runtimes/globemind-web/0.10.0`。回滚仍须按与本次相同的强身份、显式 release/runtime、先验收后提交软链流程执行，不能使用裸 `restart`。
