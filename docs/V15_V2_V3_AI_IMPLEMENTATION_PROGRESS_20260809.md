# V1.5–V3 AI 可实施能力推进记录

更新时间：2026-08-09（Asia/Shanghai）  
依据：`/root/globemind-audit-round2-2026-08-09/implementation-handoff.md` 与同目录完整审计报告  
总状态：本轮 AI 可独立实现的核心基础纵切片已收口；V1.5、V2.0、V3.0 的真实发布门禁均为 **BLOCKED**；未授权、未执行生产发布、服务启停、管线迁移或候选环境操作

## 口径与安全边界

- “代码完成”只表示仓库内 contract、存储、路由、页面和离线测试闭环，不代表真实数据、真实研究任务、真实模型质量或生产可用性通过。
- `feature registry --release-ready` 只验证公共 facade、所有权角色、证据 locator、健康/候选门禁定义和回滚记录完整；它不替代候选运行，也不覆盖数据目录、模型保障和真人验收的业务门禁。
- 权威连接器测试全部使用 mock transport；没有把配置、官方文档链接或一次成功请求冒充持续可用、许可完备或已登记数据源。
- 模型保障只接受 manifest sufficient statistics 并由服务端重算；没有运行真实模型、读取金标准数据字节或验证外部审阅物。
- 研究工作台产出的是 reviewed-draft manifest，不是正式报告、事实结论或决策建议。
- 仅修改 `/root/data/globemind` 源码仓库。未运行或导入 release、previous、rejected 或版本化发布目录中的 Python；未操作运行服务和长管线。

## 跨版本状态矩阵

| 版本任务 | 当前状态 | AI 已完成 | 仍然阻断发布的外部证据 |
| --- | --- | --- | --- |
| V1.5 三个试点国家 | BLOCKED | 研究项目 contract 最多限定三个国家并保留国家范围 | 国家选择、母语研究员、正式国家档案与任务验收均未提供 |
| V1.5 研究项目、问题、保存检索、证据包 | 代码纵切片完成 | release 外持久化、ACL、版本、查询收据/快照关联、支持/反方/背景证据、缺口与替代假设 | 来源内容与研究结论仍需研究员核验；没有许可齐备的三国资料库 |
| V1.5 报告与审阅闭环 | 代码纵切片完成 | 人工确认/修改/驳回、同行审阅、批准、版本化 manifest、结构化版本差异、四格式 reviewed-draft 下载，以及定时报告来源 ID/实质块处置门禁 | 没有真实研究员执行端到端验收；服务端未验证来源真实性/语义蕴含/事实，Word/PDF/PPT 未实现，下载物仍不是正式报告 |
| V1.5 约 200 个专业问题评测集 | BLOCKED | 检索回执、稳定实体 ID、布尔 contract 与评测存储基础已具备 | 没有专家问题集、相关性标注、语言/国家/议题金标准 |
| V1.5 第一轮模型评测 | 基础代码完成 / 数据 BLOCKED | 服务端重算 precision、recall、F1、Brier、ECE，分国家/语言/议题覆盖与阈值门禁 | 没有真实 holdout/gold bytes、独立审阅物、标注规范与一致性证据 |
| V1.5 MFA、RBAC、会话、审计 | 基础代码完成 / 外部 BLOCKED | 项目 owner/reviewer/reader ACL、RFC6238 TOTP、一次性恢复码、登录挑战、tracked JWT/jti、会话撤销和脱敏安全事件链已完成 | 企业 SSO、设备身份、外部审计锚和独立安全测试不在本地代码可证明范围 |
| V1.5 独立无障碍/安全测试 | 源码可实施部分完成 / 外部 BLOCKED | 表单、焦点、ARIA、错误状态、44px 触控、移动筛选折叠、Story Graph 键盘/列表替代和 CSP 已补齐仓库契约 | 必须由独立测试方在真实浏览器、移动设备、屏幕阅读器和授权候选环境验收 |
| V2 跨语言实体、关系、时间有效期 | 治理代码完成 / 质量 BLOCKED | 21 个稳定 URN 种子、证据门禁的实体/别名决定、关系有效期、撤回、合并/拆分及 HMAC 追加账本 | 全部种子仍 `review_required`；无消歧金标准、机构目录、真实关系证据或 ≥95% 评测 |
| V2 原文快照、处理链、撤回影响 | 代码纵切片完成 | 正文哈希、parser 版本、不可覆盖快照、修订链、claim 下游影响与人工处置 | 逐来源全文保存许可、对象存储/WORM、签名和正式保留政策未批准 |
| V2 团队协作、审阅、批准、版本比较 | 代码纵切片完成 | 项目 ACL、人工决定、同行审阅、批准、manifest 版本与差异 | 尚无机构目录、人员任命、真实团队验收和不可篡改审计服务 |
| V2 SSO、租户隔离、细粒度权限 | BLOCKED | 单应用身份与项目 ACL 可作为边界基础 | SAML/OIDC IdP、租户模型、组织策略、密钥轮换和渗透验收未提供 |
| V2 授权专业数据 | BLOCKED | 世界银行、IMF、UN SDG、Crossref 的有界 fail-closed connector 已实现 | 新闻、冲突、贸易、制裁等商业授权与法律复核未提供；四源目录仍 blocked |
| V2 冗余、容灾、值班、事故响应 | 测量/本地审计代码完成 / 外部 BLOCKED | 候选强门禁、公开状态、运行资产只读盘点、search/export/report 持久观测链和失败关闭基础存在 | 多区基础设施、供应商 SLA、值班责任人、共享存储认证、演练和恢复证据需要组织资源 |
| V2 个人信息访问、更正、删除、导出、注销 | 导出/预检/申请代码完成 / 实际删除 BLOCKED | 最小化注册、资料更正/清空、关系数据和跨助手/研究系统的有界导出；只读删除影响计划按 delete/anonymize/retain/review_required/unavailable 分类；密码确认申请与取消 | 正文和无法证明归属的范围明确 unavailable；实际删除仍需 retention、checkpoint、法律依据、他方权利判断和人工执行链 |
| V2 模型漂移、回滚、数据修订 | 代码纵切片完成 / 证据 BLOCKED | baseline hash、分层门槛、漂移比较、安全 rollback recommendation、来源修订与判断影响 | 没有真实历史回测、合格 baseline、生产模型/数据版本映射和回滚演练 |
| V3 10–20 国本地语言体系 | BLOCKED | 通用实体/研究/来源 contract 可扩展 | 数据、母语研究员、领域本体、质量基准和许可均缺失 |
| V3 海运、航空、军力、供应链、遥感 | BLOCKED | 连接器安全 transport 可复用 | 供应商、许可、专家、GIS/遥感处理链和历史真值未提供 |
| V3 人物/机构/派系/所有权网络 | 治理代码完成 / 数据 BLOCKED | 稳定实体、时态别名、证据绑定的关系、撤回、合并/拆分和人工审计链已具备 | 真实派系/所有权资料、关系证据、消歧金标准、机构目录和本地政治语境未建立 |
| V3 指标预警、替代情景、判断变化通知 | 处置代码完成 / 运营 BLOCKED | 数值预警 fail-closed、替代假设、判断/manifest 差异，以及确认、升级、误报、解决和复盘状态机已接通 | SLA、通知投递、机构事件系统、值班责任链和真实演练均未配置 |
| V3 校准、回测、漂移、独立红队 | 基础代码完成 / BLOCKED | 校准与漂移 contract、服务器重算、候选强门禁完成 | 历史样本、真实模型运行、红队团队与独立报告均未提供 |
| V3 地理分析 | BLOCKED | 现有地球页面只保留可用性握手与失败降级 | 设施/港口/基地/供应链图层、精度、许可与 GIS 专家均缺失 |
| V3 综合风险指数与“实时情报”恢复 | BLOCKED | 过期/缺源/低覆盖时停止精确出分，历史模式和 trust gate 已建立 | 必须先有真实覆盖、方法、历史回测、持续 SLO 和供应商 SLA；当前不得恢复营销定位 |

## 已完成的核心纵切片

### 可复现检索与跨语言实体

- `boolean-v1` 支持有界 AND/OR/NOT、括号、短语和隐式 AND，按 NOT > AND > OR 解释；纯否定、无正向锚点 OR、冲突条件与不支持语法返回结构化 422。
- 查询结果带规范化 contract、真实 AST/展开、实体目录版本、时间字段、页内 cutoff/coverage、有序结果 ID 和 SHA-256 回执。
- 认证用户可显式捕获 per-user append-only 查询快照；GET 不写，重放只返回当时 ID 与 contract，不执行当前查询，也不声称冻结正文或语料。
- 实体目录覆盖国家、人物、组织和地点的稳定 URN；未获人工证据的种子保持 `review_required`，准确率明确 `not_measured`。

### 权威数据连接器

- 世界银行、IMF、UN SDG、Crossref 统一 contract；HTTPS host allowlist、禁代理环境继承、禁重定向、有界 timeout/响应/解压/记录数。
- 每次成功响应携带来源、API/adapter 版本、cutoff、last success、coverage、license、payload hash；刷新失败不返回旧记录。
- 公开连接器目录固定 `not_observed`，不会把配置冒充 live；统一 data catalog 中的四条 source 记录继续被许可、owner、覆盖、质量与 provenance 门禁阻断。

### 来源快照、修订与影响

- 认证显式 capture 保存规范化正文、内容哈希、抓取时间和 parser 版本；URL 会去除凭据、query 与 fragment。
- snapshot、revision、impact review 采用 release 外 append-only、no-replace、文件锁、fsync、hash/integrity chain；符号链接、硬链接、重复 JSON key 和篡改 fail closed。
- 正文变化、更正或撤回会把既有 claim 标记为需影响审阅；管理员可确认、修改或驳回受影响 claim 集，原始记录仍保留。

### 研究工作台

- 项目具有 owner/reviewer/reader ACL、乐观版本、变更原因和 release 外持久化；损坏或版本链不一致不回退内存。
- 工作流覆盖问题、保存检索、支持/反方/背景证据、信息缺口、替代假设、判断、人工决定、同行审阅、批准和版本化导出清单。
- evidence 可只读绑定既有正文 snapshot；saved search 可只读绑定既有查询 snapshot；跨用户、部分字段、篡改和不可用均拒绝，无自动 capture。
- 两个已持久化 manifest 可按稳定 ID 比较 added/removed/modified，读取比较不创建审计事件。
- 已持久化 manifest 可确定性下载为 JSON、Markdown、HTML 或 CSV；内容只来自冻结字段，带 cutoff、方法/版本、证据、反方、缺口、审阅/批准及多层哈希，不执行查询或生成新叙事；旧 v1 缺失 scope/summary/note 明示 unavailable。
- HTML 严格转义且不含脚本、远程资源或内联样式，并由响应 CSP 约束；CSV 只导出固定列证据/引用清单并中和公式前缀，明确不是完整报告。格式分别有 16 MiB、8 MiB/5000 行和统一 64 MiB 上限，超限失败而非静默截断。

### 模型保障

- append-only model-assurance ledger 接受 confusion/calibration sufficient statistics，由服务器重算 precision、recall、F1、Brier 与 ECE。
- 强制国家、语言、议题 strata 对 overall 做完整 partition，拒绝 NaN、重复 strata、矛盾 gold claim 和不可行概率矩。
- baseline 必须精确匹配 entry hash、model/method/label/threshold/dataset；漂移只能回滚到独立复核且覆盖完整的合格 baseline。
- 空账本为 `blocked/not_observed`；所有结果是 `manifest_only`，本地哈希链不冒充 WORM、签名或外部复核。

### 身份、隐私与浏览器安全

- 注册只要求用户名、邮箱和密码；姓名/手机号可不填且可清空。导出排除密码 hash、reset/access token 和 API key 值。
- 删除申请需要当前密码和精确确认短语，只记录 `pending_manual_execution`；在跨系统 retention/checkpoint 未完成时不声称实际删除。
- Story Graph 主要按钮、层级切换、收起/展开与证据链接至少 44×44px。
- 主应用 CSP 不再允许任意内联脚本；构建阶段为 Vite/legacy 必要启动脚本生成精确 SHA-256 allowlist，外部脚本仍限同源。
- `/academic-data` 保留旧书签但明确揭示实际是 Agent Skill/连接卡市场，并链接正式 `/sources`，不再冒充论文或学术数据库。
- 登录成功默认签发可追踪的随机 `jti` 会话；启用 MFA 后密码阶段只返回有限、一次性的第二因素挑战，未完成挑战前不签发 token。TOTP secret 使用既有 Fernet 主密钥加密，恢复码仅一次展示并只持久化慢哈希。
- 个人中心可启用/确认/停用 MFA、列出与撤销 tracked sessions、查看脱敏安全事件；首次只读不会创建账本。文件链不是 WORM 或独立审计系统，界面与状态 contract 不作相反声称。
- 个人数据导出以数据库中的 canonical `user_id + username` 为主体，关系字段、单项、分区和最终响应均有字节/数量界限；`assistant_messages.extra_json` 等可能含提供商或上下文信息的字段不原样输出。
- 助手工作区只导出本人沙箱的文件 metadata、大小、时间、SHA-256 和既有认证下载 locator；计划任务与生成报告只导出可证明 `user_id` 归属的非敏感 metadata/hash。研究项目只导出当前仍有 ACL 的本人 membership、本人创建内容和本人事件，不展开其他成员身份或聚合他人正文。
- 适配器损坏、越界、路径不安全、主体不匹配或无法证明归属时标记 unavailable/partial，不伪装为空集合。删除申请仍固定 `pending_manual_execution`；仓库内没有自动执行真实删除的路径。
- 认证只读 `/api/user/privacy/deletion-impact-plan` 复用上述有界导出，只返回 scope、处置类别、计数/计数状态、归属依据代码和原因代码；账号主记录需复核、共享研究作者内容计划匿名化、共享容器保留，安全账本/备份/日志与截断范围保持 unavailable。
- 删除预检固定 `operation_mode=read_only_preflight`、`deletion_performed=false`、`execution_state=blocked`，并列出 retention/legal basis、checkpoint/recovery、共享依赖和 manual authority 阻塞。五条 privacy route 均只接受数据库匹配的真实正整数 `user_id` 与 canonical username；bool、字符串或旧 `id` 别名 fail closed。

### 服务测量与预警处置

- search、个人数据导出、研究报告下载只按预登记的 ASGI 路由模板记录 `scope/outcome/duration/timestamp`；query、动态 ID、正文、认证头和异常详情不进入账本，未知路由不采集。
- release 外服务测量账本使用 flock、no-replace、fsync 和哈希链；采集失败不改写业务响应，并进入独立有界失败计数。24 小时汇总重算计数、成功率与 nearest-rank P50/P95/P99。
- 目标固定 `not_approved`、合规固定 `not_computable`；公开状态页只显示脱敏样本和分位数，并明确“有观测不等于达标”。没有经组织批准的目标、误差预算和 approver evidence。
- 金融告警处置账本绑定真实历史告警哈希，采用严格 `open → acknowledged → escalated/false_positive/resolved` 状态机和 terminal 后一次复盘；普通认证用户只见理由哈希/长度，管理员 API 才能读取完整审计或写入。
- 所有处置写入复用金融可信门禁；历史模式仍可读但不可变更。公共告警页仅投影状态、流转次数和是否复盘，不公开 actor/reason。SLA、通知投递和机构事件系统保持 unavailable/not configured，本地哈希链不称为 WORM。

### 定时 AI 报告来源边界

- 无人值守定时报告只使用用户固定收藏中带安全 locator 和足够摘要的有界来源 inventory；无合格来源时不调用模型。每个实质内容块必须带 `[GM-Sxx]` 或显式 `[GM-UNKNOWN]`，越界引用、无处置内容、原始 HTML、Markdown 图片和远程自动加载资源 fail closed。
- 服务端分别记录“有来源标记的实质块比例”和“来源或显式未知的处置比例”；显式 unknown 不再被计算为引用覆盖。成功产物仍固定 `review_required/blocked_pending_human_review`，并水印说明来源真实性、语义蕴含、事实准确性与人工批准均未验证。
- 写入时保存模型输出和最终水印草稿的 SHA-256 指纹；互动聊天与定时报表产品路径均已强制显式 claim 数组、服务端 claim ID 与逐 claim 来源/unknown 边界。UI 同时固定显示本地 Markdown/JSON 可变、读取时未重验、append-only/WORM 审计链 unavailable；其他 Agent 产出、来源真值、语义蕴含与事实准确性仍不在该门禁证明范围。

### 时态实体人工治理

- 认证工作台从 Search facade 读取版本化种子，管理员写入只接受 canonical `user_id`，并要求已验真的正文 evidence snapshot；默认种子不会因进入工作台自动获批。
- 状态机支持实体 approve/reject、带语言/上下文/有效期的别名审阅、SPO 关系新增/撤回，以及保留历史的 merge/split；未知实体、自环、冲突时间、stale head 和缺证据请求 fail closed。
- release 外账本执行完整 SHA-256 与 HMAC 链校验，拒绝 symlink、hardlink、重复 JSON key、删链和篡改。当前没有 key ID、双钥验证、数字签名、WORM 或机构目录，因此真实实体质量仍然 blocked。

## 候选强门禁

- `data_governance_catalog`：正式 catalog 必须 ready、非空、ID/kind 唯一一致、全部 eligible、blocked=0。
- `article_evidence_chain`：抽样判断必须与真实 reader 正文段落、anchor、excerpt 和 body hash 交叉核验。
- `research_storage`：短期候选身份只读验证 atomic JSON/fsync、无内存 fallback，并明确 audit immutability unavailable；不创建项目。
- `model_assurance`：要求至少 baseline+current 两条评测、至少一条 eligible、gold review manifest attested、latest gate eligible、drift within threshold、rollback proceed、无 reason codes。
- `identity_assurance`：要求 MFA enrollment 的 Fernet 能力和 release 外存储可用、读取零写，并核验安全审计只输出哈希/长度等脱敏字段；不要求候选账号实际启用 MFA。
- `identity_deletion_impact_plan`：只读核对删除影响计划的处置分类/汇总、外部 blocker、严格非执行状态和敏感字段缺失；不登记申请，也不执行删除、匿名化或保留裁决。
- `service_level_status/summary`：核对完整链派生状态、三类固定工作流、计数/率/分位数一致性和敏感字段缺失；固定拒绝把未批准目标写成达标。
- `entity_governance_status/catalog`：只读验证存储/完整性/写入能力契约、种子 review 状态和未测量准确率；不执行治理 mutation，也不把零 approved 冒充质量通过。
- `financial_alert_triage`：只读核对公共告警历史中的闭集状态、流转引用、历史/可写互斥和 operational limitations；拒绝 actor、理由和完整审计泄漏，不执行任何处置写操作。

这些是候选验证器的代码契约。当前源码生成的正式 catalog 登记仍全部 blocked；仓库内也没有可证明候选环境已具备真实金标准评测的 evidence。未读取或运行候选外部账本，因此不能声称候选通过；除非候选现场同时提供 eligible catalog 和合格 model-assurance 记录，否则门禁会失败。离线 FakeCandidate 测试通过不能改写这一结论。

## 最终工程验证

最终源码仓库快照：锁定 Web runtime 全量后端 1446/1446（仅 3 条既有弃用警告）；candidate validator 离线 FakeClient 80/80、49 个 required HTTP checks；Vue feature tests 176/176、全量 ESLint 通过；金融 trust/triage 8/8、TypeScript typecheck 通过。feature registry 为 18 features、10 owners、28 public entries、37 route namespaces、20 route modules、20 pages、19 dependency edges、54 verified records、0 boundary violations，`--release-ready` 仅架构层通过。Vue 主应用生产构建 4270 modules/3 分 11 秒、金融生产构建均通过；`git diff --check` 通过。

上述 FakeClient、pytest、Node、ESLint、TypeScript 和本地 production build 都是离线源码验证。未请求真实候选 URL，未执行真实浏览器/移动设备/屏幕阅读器验收，未启动或切换服务，也未生成可作为候选接收结论的 `acceptance.json`。生产构建的 assets/index 中未发现 `/showcase` 或 `DeltaForceStudio`；这不替代候选 fallback/status/browser 门禁。

## 必须由组织/外部资源完成的下一步

1. 任命具名数据、模型、隐私、安全、服务和研究负责人，并提供可审计授权记录。
2. 由法务逐来源确认许可、全文快照、保留、跨境、删除和模型训练用途；当前 contact 邮箱只负责受理/转交。
3. 建立三个试点国家的母语研究团队、约 200 个专业问题与真实端到端验收任务。
4. 提供真实 gold/holdout 数据字节、标注手册、一致性报告、模型运行产物和独立 review artifact，再提交保障 manifest。
5. 接入 SSO/IdP、租户、对象存储/WORM、监控、灾备、值班和供应商 SLA，并进行渗透、无障碍与 AI 红队。
6. 在隔离候选环境运行 HTTP、桌面/移动浏览器、离线、stale/empty/error/timeout 和组合权限门禁；另行授权后才可进入发布 runbook。
