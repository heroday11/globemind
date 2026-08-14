# V1.0“可信数据基础版”实施进度

更新时间：2026-08-09（Asia/Shanghai）  
依据：`/root/globemind-audit-round2-2026-08-09/implementation-handoff.md` 与同目录完整审计报告  
状态：首批基础纵切片已完成并通过离线测试；V1.0 总发布门禁仍为 **BLOCKED**；未授权、未执行生产发布或服务操作

## 安全与口径

- 仅修改 `/root/data/globemind` 源码仓库，保留工作树中的用户既有改动。
- 未运行或导入 `current`、`previous`、版本化或 rejected release 中的 Python；未修改发布证据。
- 未停止、重启、接管或迁移服务和长管线，也未访问或输出凭据、连接串、进程参数或环境变量值。
- Python 验证使用锁定 Web 运行时 `/root/data/python-runtimes/globemind-web/1.0.0/bin/python`，启动前设置 `PYTHONDONTWRITEBYTECODE=1` 并使用 `-B`。
- 本文只把成果称为“代码纵切片完成”；这不表示真实数据登记、候选环境、浏览器、外部服务或生产发布已验收。

## V1.0 总览

| 交接任务 | 当前状态 | 已取得的代码证据 | 未完成门禁 |
|---|---|---|---|
| 统一 `live / delayed / stale / offline` | WIP（首批纵切片） | 公开 `/api/status`、前端共享 freshness、数据目录均使用闭集并 fail closed | 目前仅公开验证 search、Ground News、opinion；仍需全数据集覆盖、候选持续观测与桌面/移动一致性验收 |
| 数据源目录、数据卡、模型卡、许可证登记 | WIP / BLOCKED | 建立统一 catalog schema、公开只读 API、`/sources` 数据卡、四个权威连接器登记和重新计算门禁 | 9 个默认记录当前仍为 0 eligible；负责人、许可、质量、覆盖、版本、溯源等证据未补齐 |
| 新鲜度、搜索、导出、报告 SLO | 测量代码完成 / 目标 BLOCKED | 新鲜度目标由真实 lag/SLA 生成；search/export/report 使用 release 外追加账本、固定路由模板采集与 24 小时聚合；状态页公开样本、成功率和 P95 | 没有经组织批准的目标、误差预算或 approver evidence，因此合规固定为 `not_computable`，不得称为 SLO 达标 |
| 日志、指标、调用链、watermark、合成监控、状态页 | WIP | 安全 request ID、模板化请求日志、跨 worker 文件账本、公开状态页、候选 HTTP 合成门禁 | 无分布式调用链、完整数据 watermark、事件历史/订阅、多节点文件系统认证或持续候选观测 |
| 主要结论绑定文章与正文段落 | WIP | dashboard 分析与 reader 输出 claim-level evidence；显式 capture 已建立持久正文快照、修订和下游影响账本 | 尚未覆盖所有主要判断；逐来源全文保存许可、历史覆盖和外部 WORM/签名仍缺失 |
| 区分信息、假设、判断、未知和观察指标 | WIP（文章契约） | 后端与前端文章证据链使用五种闭集类型，未知输入降级为 `unknown` | 没有全局主张清单或“主要判断全部分类”的覆盖测量；仍需人工审阅责任链 |
| 高频国家、人物、组织、地点中英文别名 | 治理代码完成 / 质量 BLOCKED | 21 个种子有版本化别名、稳定 URN；新增 evidence-backed approve/reject、别名复核、时态关系、撤回、合并/拆分和 HMAC 追加账本 | 全部种子仍 `review_required`；无同名消歧金标准、转写覆盖、机构目录或 ≥95% benchmark，不能证明真实库跨语言召回一致 |
| 查询解释、短语、布尔、排除词、时间筛选 | 基础代码完成 / 评测 BLOCKED | `boolean-v1`、短语、隐式 AND、括号、NOT、实体展开、query receipt/snapshot 和 published/event 时间语义已接通 | 邻近、字段权重、通配/正则明确 422；无真实负载、召回和相关性评测，不能宣称研究级检索质量 |
| 世界银行、IMF、联合国、Crossref 首批开放数据 | 连接器代码完成 / 数据 BLOCKED | 四源统一有界 connector、认证查询 API、静态 `not_observed` 目录和正式 catalog 记录已接通 | 测试只使用 mock transport；许可、持续可用性、覆盖、质量和具名 owner 未获外部证据，四条 source 记录保持 blocked |
| 隐私处理与 API/依赖资产盘点 | WIP / BLOCKED | 管理员只读 API/OpenAPI、3 份依赖锁清单、环境变量名和 7 项处理活动 inventory 已接通并拒绝敏感值 | 7 项 owner 均待指定，retention/legal basis 未批准、processor inventory 未完成；102 个 Python 依赖许可仍 unknown |

## 已实现的基础能力

### 数据与模型登记

- 新增 `/api/data-governance/catalog` 及单记录查询，统一登记 dataset/source/model 的 owner、version、operational、freshness、coverage、license、quality、provenance、schema 和重算 status。
- 技术可用性、业务新鲜度、研究就绪三层分离；缺失、损坏、互相矛盾或无法验证的证据不会被推断为可用。
- `/sources` 使用前端第二道 sanitizer；请求开始、失败或契约损坏时立即清空旧目录，generation gate 阻止晚到响应回填。
- 候选门禁要求 catalog `ready`、记录全部 eligible、ID 唯一、kind/前缀一致且汇总一致；`incomplete` 虽可公开查看，但不能通过发布验收。

当前默认 catalog 为：9 records（3 dataset、5 source、1 model）、0 eligible、9 blocked、`formal_release_status=blocked`。新增的 World Bank、IMF、UN SDG 与 Crossref 记录只表示 checked-in connector，不表示 live。共同阻断包括具名负责人、变更记录、覆盖、截止时间/最后成功时间、许可、质量、溯源与 schema 治理；来源 CSV 的聚合清单也不能替代逐源许可和覆盖证据。该状态是诚实的未完成门禁，不是故障掩盖。

### 文章证据链

- dashboard 分析与 `/api/article/{news_id}/reader` 输出 `article-evidence-v1`，包含五类 claim、正文段落号、稳定 anchor、关系类型、matched text、excerpt 和正文 SHA-256。
- 标题不能回退为正文证据；找不到正文锚点时 claim 保持 `unavailable` 并给出原因。
- 显式认证 capture 可保存规范化正文、采集时间、parser version 和内容哈希；修订、更正或撤回会产生不可覆盖 revision/impact 记录。未 capture 的历史内容仍明确 unavailable，不生成占位 ID。
- 候选黑盒门禁从真实 reader body 重新分段和计算哈希，要求抽样文章至少有一个正文支持的判断；伪造 excerpt、标题文本、段落号或哈希均不能通过。

### 检索、实体与时间语义

- 新增国家、人物、组织、地点通用版本化目录与稳定 URN；所有缺人工证据的种子保持 `review_required/accuracy_claim=not_measured`。
- `boolean-v1` 支持有界 AND/OR/NOT、括号、双/弯引号短语和隐式 AND，按 NOT > AND > OR 解释；纯 NOT、无正向锚点 OR、不平衡语法和不支持的邻近/正则/通配符/字段查询返回结构化 422。
- `query_explain` 只报告真实最终命中与已应用步骤，不伪造中间计数，不把 exact 静默放宽为 fuzzy。
- news 时间筛选使用 `published_at`；L1/L2/L3 使用 `event_time` 区间重叠；采集/更新时间明确不可筛选。
- 查询结果生成包含规范化 AST、版本、cutoff/coverage 和有序结果 ID 哈希的 receipt；认证用户可显式捕获 per-user append-only snapshot，重放只返回当时 ID/contract，不声称冻结正文或语料。

### 状态、可观测性和工程资产

- 新增无需登录的 `/api/status` 与 `/status`；公开响应仅含 search、Ground News、opinion 三项研究能力及有限 SLO 证据，不暴露表名、依赖、调度器或内部延迟。
- 详细 `/api/health/features` 要求有效登录身份并供短期候选 token 使用；缺能力、schema 损坏、null 数值或根/子状态矛盾均 fail closed。匿名研究者只读取脱敏 `/api/status`。
- 每个 API 请求生成或校验有界 `X-Request-ID`；日志只记录请求方法、静态路由模板、状态和耗时，不记录 query、动态路径值、认证头或异常正文。
- search、个人数据导出和研究报告下载通过预先登记的 ASGI 路由模板写入 release 外服务级观测链；只持久化工作流类别、结果、毫秒时长和时间。未知路由不采集，写入失败不会替换业务响应，而会进入独立有界失败链或显式 unavailable。
- `/api/service-level/status` 与 `/summary` 要求登录，读取会验证完整链并重算 overall/分工作流计数、成功率和 nearest-rank P50/P95/P99。公开 `/api/status` 只投影脱敏聚合；目标固定 `not_approved`、合规固定 `not_computable`，不会把样本存在冒充 SLO。
- 管理员 `/api/governance/asset-inventory` 盘点实际 API、锁文件依赖、环境变量名和隐私处理活动；`/api/governance/openapi.json` 暴露当前运行 schema。manifest symlink、重复 JSON key 或损坏内容会被拒绝。
- feature registry 已登记 data-governance、evidence-chain、service-level 与 entity-governance，并将新增治理路由纳入所有权；跨 feature 引用统一走 public facade。

### 时态实体人工治理

- 认证只读 `/api/entity-governance` 工作台展示 21 个 Search facade 种子、批准投影、关系和历史；所有默认种子保持 `review_required/accuracy_claim=not_measured`，不会因进入工作台自动获批。
- 管理员 mutation 只接受 canonical `user_id`，并要求已验证正文快照引用；支持实体决定、带语言/上下文/有效期的别名复核、SPO 关系新增/撤回及无破坏合并/拆分。未知、未批准、自环、冲突时间和 stale head 均拒绝。
- release 外账本采用 flock、no-replace、fsync、SHA-256 与独立 HMAC 链；读取零写，symlink/hardlink/重复 key/删链/篡改 fail closed。它明确不是 WORM、数字签名或机构目录，当前 HMAC 无 key ID/双钥轮换，直接换 key 会使旧链不可验证。

## 发布门禁现状

V1.0 交接规定“所有正式数据集必须具备负责人、版本、时效、覆盖、许可证和质量状态；主要判断必须具备基础来源引用”。当前两半均未完全满足：

1. catalog 的正式登记门禁为 blocked，默认 9 个记录全部不具备完整外部证据；候选 smoke 已配置为因此失败。
2. 证据快照与影响账本已覆盖首个纵切片，但仍不能证明所有主要判断、历史报告和助手回答均可追溯。

因此当前工作树可以称为“V1.0 基础能力首批实现”，不能称为“V1.0 已发布”“研究级就绪”或“实时情报系统”。

## 工程验证

- 锁定 Web runtime 全量后端：1446/1446，通过；仅 3 条既有 Starlette/Pydantic 弃用警告。`FakeCandidateClient`/validator 专测 80/80，当前黑盒合同为 49 个 required checks；未请求候选 URL、未核验 release identity、未生成真实候选 `acceptance.json`。
- feature registry：18 features、10 owners、28 public entries、37 route namespaces、20 route modules、20 pages、19 dependency edges、54 verified records、0 boundary violations；`--release-ready=true` 仅表示 facade/所有权/门禁证据记录闭合，不覆盖 data catalog 的正式发布阻断。
- Vue feature tests：176/176；全量 Vue ESLint 通过。
- 金融 React trust/triage tests：8/8；TypeScript typecheck 与生产构建通过。
- Vue `build:main-only`：4270 modules，3 分 11 秒，通过；生产 assets/index 中无 `/showcase` 或 `DeltaForceStudio`，仅有既有 chunk-size warning。`git diff --check` 通过。

上述均为源码仓库离线验证，不代表候选环境、真实数据库内容、真实浏览器或生产环境通过。锁定 Python runtime 未安装 Ruff，因此本轮未执行 Ruff；使用 pytest、架构门禁、ESLint、TypeScript 和生产构建覆盖改动。

## 后续优先级

1. 由组织正式任命数据、模型、服务、隐私和安全负责人，并录入可审计授权证据。
2. 为 9 个默认 catalog 记录补齐许可、覆盖/缺失/重复指标、版本与变更记录、质量评测、数据字典和 schema 映射。
3. 为现有文章快照/修订账本补齐逐来源许可、历史 capture 覆盖、外部不可篡改锚和全局主要判断覆盖率。
4. 由责任组织基于已落地的持久观测批准搜索/导出/报告目标、误差预算和事件响应流程；在批准前保持 `not_computable`，并补多节点存储、事件历史和持续合成监控验收。
5. 扩充人物、组织、地点别名与消歧；建设 200 个专家查询集及分语言 Recall@100、nDCG@20 评测。
6. 在许可和任务范围明确后，对四个已实现 connector 做候选网络、限流、长期可用性和覆盖验收，再把对应记录从 blocked 提升；不得用 mock 结果晋级。
7. 在独立候选环境运行强门禁、桌面/移动浏览器、stale/empty/error/timeout、离线与可访问性组合回归；另行授权后才进入发布 runbook。
