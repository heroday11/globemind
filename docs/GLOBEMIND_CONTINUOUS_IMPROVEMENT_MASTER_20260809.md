# GlobeMind 持续优化总控与跨会话交接

更新时间：2026-08-10 08:09 UTC（工作区系统时钟）  
执行目录：`/root/data/globemind`  
任务状态：**FINAL CLOSEOUT COMPLETE（2026-08-10 08:09 UTC）；最终聚焦/全后端/九项离线验证与 130 项审计已复跑，登记状态不变，后续工作转交真实数据、人工批准和外部候选环境**  
12 小时计时锚点：2026-08-09 16:04:29 UTC  
最早允许收口时间：**2026-08-10 04:04:29 UTC**（北京时间 2026-08-10 12:04:29）  
依据：

- `/root/globemind-audit-round2-2026-08-09/implementation-handoff.md`
- `/root/globemind-audit-round2-2026-08-09/comprehensive-10-round-audit.md`
- `docs/V09_TRUSTED_EMERGENCY_PROGRESS_20260809.md`
- `docs/V10_TRUSTED_DATA_FOUNDATION_PROGRESS_20260809.md`
- `docs/V15_V2_V3_AI_IMPLEMENTATION_PROGRESS_20260809.md`
- `docs/operations/V010_ACCEPTANCE.md`
- `docs/operations/FINAL_CLOSEOUT_HANDOFF_20260810.md`
- `AGENTS.md`

## 0. 新会话必须先做什么

1. 完整读取本文件和仓库根目录 `AGENTS.md`，再读上述审计、交接和三个版本进度文档。
2. 检查当前工作树和本文件“连续执行日志”；现有改动全部视为用户资产，不 reset、不覆盖、不为通过测试而丢弃。
3. 创建或续接持续目标，终点不得早于 2026-08-10 04:04:29 UTC；若接管时间已超过该时刻，仍须完成未闭环的验收矩阵，而不是立刻宣布结束。
4. 先验证前一会话留下的 WIP，再选择最高优先级未完成项；不要从头重写已经有证据的纵切片。
5. 每 45–90 分钟更新一次本文件的日志、状态矩阵和测试证据。连续两轮不得只做同一小点，除非它仍是明确 P0。

## 1. 总目标

把 GlobeMind 从“功能很多但数据、证据和质量声明不足的实验平台”，持续推进为：

> 一个不会伪装实时、不会在数据不可信时硬算分、查询可解释、结果可追溯、模型可评测、研究过程可复现、权限和隐私边界明确，并能持续发现自身缺陷的全球新闻研究平台。

最终成功不以“页面能打开”或“测试变绿”为准，而要同时满足：

- 产品表述与真实能力一致；没有未经证据支持的实时、覆盖量、语言量、可信度或专业结论声明。
- 每个关键结果都有来源、截止时间、覆盖、方法/模型版本和不可计算原因。
- 搜索对自然语言、短语、布尔、实体、时间和排除条件有明确且一致的语义，并有真实相关性评测。
- 数据质量可以离线复算；阈值、金标准和人工批准缺失时不能自动宣称通过。
- AI 生成物带 prompt/model/source 版本和引用边界；无证据时明确未知，不补造引用。
- 数据目录、来源许可、owner、质量、覆盖、schema、provenance 全部通过后，数据才可 release eligible。
- 研究项目、证据、反方证据、判断、审阅、批准和导出可复现，但 AI 不冒充研究员或事实核验员。
- MFA、会话撤销、ACL、隐私导出/删除预检、审计和候选门禁 fail closed。
- 真实候选环境、浏览器、移动设备、屏幕阅读器、负载、红队和法务验收有独立证据。
- 任务结束后仍可由定时只读审计自动发现回归；自动修复只能形成隔离补丁/PR，不得自动发布到生产。

## 2. 不可突破的安全边界

- 禁止运行或导入 `/root/data/releases/globemind/current`、任一版本化 release、`previous` 或 `rejected` 中的 Python。
- 所有 Python 诊断必须从启动前设置 `PYTHONDONTWRITEBYTECODE=1`，并使用锁定运行时：

  ```bash
  PYTHONDONTWRITEBYTECODE=1 /root/data/python-runtimes/globemind-web/1.0.0/bin/python -B ...
  ```

- 不启动、停止、重启、接管或迁移服务和长管线；不根据 PID 文件猜测进程；不输出参数、环境变量、数据库 URL、token 或 secret。
- 不访问真实候选、生产数据库、外部付费源或需要凭据的 API，除非用户另行明确授权。
- 不修改发布证据；不部署；不调用真实通知、删除、告警处置或管理员 mutation。
- 只在源码仓库或隔离临时目录工作；修改文件使用精确补丁；保留所有无关脏工作树改动。

## 3. 当前真实状态

### 3.1 已有工程基础

以下是已经落地的主要能力，但“代码完成”不等于“真实数据/候选/生产验收完成”：

- V0.9 可信止血：历史模式、freshness/trust gate、金融/舆情不可计算、过期与缺源降级、Story Graph 迟到响应隔离、生产移除 showcase、公开帮助/隐私/条款/安全/更正入口、认证表单无障碍基础。
- V1.0 数据治理：公开状态、数据 catalog/data card、来源/模型/版本/覆盖/许可/质量契约、World Bank/IMF/UN SDG/Crossref 四个有界连接器、正文证据快照、修订和影响链、服务观测账本。
- 搜索基础：参数化 SQL、短语和 Boolean AST、版本化实体别名、稳定实体 ID、查询解释、时间字段语义、结果回执和个人查询快照。
- V1.5 研究工作台：项目 ACL、问题、保存检索、支持/反方/背景证据、缺口、替代假设、人工决定、同行审阅、批准、版本比较和 JSON/Markdown/HTML/CSV reviewed-draft 导出。
- 模型保障：从 sufficient statistics 重算 precision/recall/F1/Brier/ECE，检查 strata、baseline、漂移和回滚；没有真实 gold 时保持 blocked。
- 身份与隐私：TOTP MFA、恢复码、tracked session/jti、会话撤销、个人数据导出、删除影响只读预检和人工删除申请。
- 运行与处置：search/export/report 服务时延账本、公开状态摘要、金融告警确认/升级/误报/解决/复盘状态机。
- 定时报告引用边界：来源清单、有引用块率与处置率、显式 unknown、水印、写入时 SHA；不声称事实/蕴含已验证。

### 3.2 最近一次大范围离线基线

这是当前增量开始前的最近一次可复现快照，不能替代本轮最终复跑：

- 后端：1446 passed，3 条既有弃用 warning。
- Vue feature：176/176；全量 ESLint 通过。
- 候选验证器 FakeClient：80/80，49 个 required HTTP checks。它只证明验证器逻辑，不代表真实候选通过。
- 金融 trust/triage：8/8；TypeScript typecheck 和两个生产构建通过。
- 主 Vue 构建：4270 modules；产物不含生产 showcase 路由/chunk。
- feature registry：18 features、31 public entries、18/13 个后端/前端 facade、22 pages、54 verified records、0 boundary violation。`--release-ready` 只表示架构登记闭合。

当前工作树又有新改动，最终必须重跑相称的组合/全量门禁后才能引用新的数字。

本轮接管后的增量前基线（2026-08-09 16:32 UTC）：后端 1459/1459、Vue feature 177/177；后端仍只有 3 条既有依赖弃用 warning。该快照是在本轮新修改与并行 WIP 完全合流前取得，只用于定位回归，最终数字必须在收口时重跑。

### 3.3 当前最重要的阻断事实

- 默认 data-governance catalog 仍是 **9 records / 0 eligible / 9 blocked**；因此 V1.0 发布总门禁仍是 BLOCKED。
- 没有 200 个专家问题、qrels、Recall@100、nDCG@20、MRR、零结果率和真实负载报告；不能声称搜索质量已经显著提高。
- 当前搜索不是完整的“一句话自然语言研究查询”：Boolean/短语/别名已增强，但中文分词、意图/时间/排除条件解析、词法+向量融合和重排尚未形成经评测的统一路径。
- 实体种子只有 21 条，全部 `review_required`，accuracy 明确 `not_measured`。
- 没有真实 gold/holdout bytes、标注手册、一致性报告、独立 review artifact 或合格 model baseline。
- 四个官方连接器只是安全代码与静态登记，公开 catalog 固定 `not_observed`；许可、owner、持续可用性和候选观测仍不足。
- 缺三国母语研究团队、具名 owner、法务许可结论、批准的 SLO、企业 SSO/租户、多区容灾、WORM/签名、独立渗透/无障碍/AI 红队。
- 没有授权运行真实候选、真实浏览器或生产服务；本轮不得把离线测试写成上线验收。

### 3.4 本轮已确认和已修复

- 新增离线新闻质量剖析：`scripts/news_ingest_quality.py`、`scripts/profile_news_quality.py`、`backend/tests/test_news_quality_profile.py`。profile contract 已升级为 v3：统计缺失、坏日期、页面型伪文章、精确 URL/正文重复、来源/语言/月切片、schema drift、publication cutoff，并加入 `bounded-char5-bottom32-rolling64-lsh8-v1` 近重复候选观测。
- 近重复计算最多评估 20,000 行、每行 4,096 字符、每桶 64 行和 1,000,000 次候选对比较；默认比较预算 200,000，溢出和跳过数必须显式披露。它不保留正文、URL、记录 ID 或指纹，输出固定 `candidate_threshold_approval_state=not_approved`、`human_review_state=not_provided`、`duplicate_fact_state=not_established` 和 `release_decision=not_computable`，因此候选对绝不称为已确认重复。
- 根复核又关闭 CLI 错称 SimHash、可绕过最大行数/输入字节、`1e400`/过深 JSON 和损坏 comparison profile 的 fail-open；硬上限为 100,000 行、128 MiB 输入、4 MiB 单行、32 层/100,000 JSON 节点和 16 MiB comparison artifact。聚焦 Python 31/31，quality/architecture/registry/candidate 交叉 144/144。没有批准期望量、更新节奏、阈值或人审标签时 interruption/freshness/release 仍固定 `not_computable`。
- 对仓库内 `wave1_18domains_extract_360_v3_articles.jsonl` 做了首次只读样本观测：358 条、19 域；67 条（18.7151%）触发机械规则，其中页面型 URL 48、短正文 18、页面型标题 3；cutoff 为 2026-06-21。它只代表该样本，不代表全库，不验证事实准确性。
- 用 v3 对三个明确仓库样本只读复算：20 条样本为 0 条机械缺陷、0 个近重复候选对；358 条 Wave1 为 67 条机械问题、2 个近重复候选对；6376 条日样本为 769 条机械问题，受 200,000 比较预算和 64 行桶限制观察到 599 个候选对、至少跳过 298,915 对并显式标记两类 overflow。三个样本的域数/cutoff 与前述 v2 观测一致，互非同 corpus baseline/current，不作相互质量升降比较；所有候选均未经人审，不验证重复事实、事实准确性、许可或来源可靠性，临时 no-replace artifact 也不是长期证据库。
- 修复舆情页无数据时先把“今天”写进结束日、异步数据回来后不跟随最后可用日的问题；人工选择的历史结束日仍保留。相关 Node 与 onboarding 共 16 passed，聚焦 ESLint 通过。
- 修复舆情反馈 DTO 与趋势工具把缺失 `impact_index/sentiment` 转为 0 的语义错误：反馈保持 `null`，趋势缺口保留为 gap，异常只比较相邻有限值，缺失分数不进入“高影响”排序或 sparkline。舆情 Node 14/14，聚焦 ESLint 通过；仍需真实浏览器验证图表 gap 呈现。
- 搜索 WIP 已收口：L3 两条直搜、L2 直搜和两套 L3→L2 children 不再用 `family_group/event_family/pair_key` 猜测 initiator/target；层级预设/切类型清除 `pub_time` 排序和新闻专属筛选。独立复核又执行了全部 6 个可见预设的同对象/可编辑/残留清理/DTO 矩阵，以及 direct、V11、legacy 的 classification 毒值映射矩阵；根复跑 Node 24/24、Python 116/116，因此 SR-05/SR-06 只在当前源码级升为 `PROVEN_CODE`，不包含真实浏览器/DB/召回质量。地理扩审又关闭 `country→language` 的五处 fallback/explain 伪映射，泛化 country 现在进入 provider/DB 前 422；新闻响应、旧适配器、分析 metadata 和详情页分列语言、来源国/地区、新闻地区及显式 legacy location，缺失保持 null。新增纯离线 `search-eval-v1` 计算 P@k、Recall@k、nDCG@k、MRR、zero-result、timeout 和 completed-only nearest-rank P50/P95；silver/human gold 不合并、unreviewed qrels 不计分、最多 1000 问题、k 不接受 bool/浮点/字符串强制转换，且固定 `not_approved/not_computable/not_established`。地理跟进专项 Python 13/13、搜索/故事图 Node 32/32；四角色存储、索引、过滤、完整 API/UI 仍未贯通。
- SR-08 四类时间语义已贯通当前搜索源码纵切片：响应和 UI 分列新闻发布日期、事件起止、采集与更新时间；`pub_time/created_at` 只作 legacy/unverified，不得跨义回退，canonical 缺失或矛盾会 fail closed；歧义 `sort_by=time` 被拒绝，历史、排序说明、详情/助手/收藏/导出标签按新闻或事件类型显示。根合流复跑 Python 61/61、Node 23/23；存储迁移、索引和历史回填尚无真实证据，事件/采集/更新时间当前多为 null，发布日期也只是未核验源值，因此 SR-08 仍为 `PARTIAL`。
- 身份配置入口已用失败测试复现并关闭 provider URL/JSON 歧义：新写入只接受无凭据、无 query/fragment/control/backslash 的绝对 HTTP(S) URL，同时保留显式 loopback/内网自托管兼容；嵌套图片配置同样拒绝重复 JSON key、`NaN/Infinity` 和不安全 URL。遗留脏值在个人资料、隐私导出和 AKM 传递侧 fail closed，不再回显或转交；请求侧旧值闭环仍随互动助手 WIP 做最终组合核验。
- 已建立 `ops/audit/registry.json` 的 130 项逐行机器矩阵和 `scripts/continuous_audit.py` 离线只读 runner。21:18 UTC 在新增 FR-09 catalog 投影和 EV-05 方法卡证据的同时，仅将有完整行为矩阵的 SR-05/SR-06 从 `PARTIAL` 升为源码级 `PROVEN_CODE`；当时快照为 48 `PROVEN_CODE`、1 `OBSERVED_SAMPLE`、61 `PARTIAL`、10 `EXTERNAL_BLOCKED`、10 `NOT_STARTED_OR_UNVERIFIED`。根复核发现原 v1 的 `code_sha` 只是 HEAD，却未披露当前大量 WIP；红灯后将报告升为 `continuous-audit-v2`，去掉顶层歧义 `code_sha`，在 `source_revision` 分列 HEAD 与 dirty worktree，不输出文件名且不声称未计算的 worktree hash。22:24 UTC 的历史 no-replace 报告核验 130/130、266 locator、156 evidence、0 stale，位于 `/tmp/globemind-continuous-audit-v2-20260809-2220-final.2XIPrJ`，重复写入 rc=2。两个诚实 finding 为 `automation_state=not_configured` 和 `AUDIT_DIRTY_WORKTREE_UNATTESTED`；临时目录不是长期 artifact retention。
- 新增受界、只读的公开声明扫描器 `scripts/ci/check_public_claims.py` 与机器策略 `config/public-claim-policy.json`；覆盖无证据的实时/今日、准确率/专业结论、数量/语种/来源和最高级声明。根代理扩审又以失败测试关闭 `aria-live` 属性为页面 `LIVE` 声明洗白、后半句限定词为前半句洗白，以及策略/源码/证据文件越界或 hardlink、重复 JSON key/非有限数值 fail-open。专项 13/13，实际仓库扫描为 `automation_state=not_configured` 且 0 findings；这只是静态源码门禁，不替代法务、owner 或运行时页面验收。
- Ground News 首页不再把缺失统计显示为 `0`/`0%`：缺值显示待核验，零分母显示不可计算，合法零值仍保留，并用 nullish 语义避免有效的 `product_candidate_count=0` 被候选数替换。Node 11/11 与聚焦 ESLint 通过；真实浏览器/屏幕阅读器仍未验。
- EV-05 新增 `ground-news-source-profile-v1` 受界方法卡：来源页、相似来源、搜索来源和 story comparison 公开出口都不再返回 raw `evidence_note`，未知 profile/method/enum 失败关闭；`evidence_url` 只允许无凭据 HTTP(S) 并清除 query/fragment。blindspot 不再输出“事实性风险/低可信文章”推断，只显示第三方目录标签构成并固定事实准确率/来源可靠性未建立。红灯 6 条及搜索出口追加 1 条后，根复跑 backend 13/13、Ground News Node 17/17，实际 public-claim scanner 0 findings。第三方目录真值、人工评级证据、全站 inventory 和候选浏览器仍缺，故保持 `PARTIAL`。
- FR-09 的 data-governance catalog 现可接收显式离线 `news-quality-profile-catalog-projection-v1`；必须通过 canonical SHA-256、固定 scope/method、时间、完整/无截断/无溢出与严格数值类型矩阵，否则清空指标。通过时也只是 `mechanically_validated`、`quality=unknown`、`coverage=partial`、release blocked；默认 route 不加载 artifact。二审又关闭等值 float 计数类型混淆，专项 30/30、quality/profile/catalog 83/83（1 条既有 warning）。SHA-256 非签名/producer attestation/row lineage，也无全库/gold/批准阈值，故 FR-09 仍为 `OBSERVED_SAMPLE`。
- QA-12 全局 freshness 提示已改为文档流内、可折叠且保持 44px 操作目标的响应式组件；支持键盘焦点、ARIA、reduced-motion、认证/路由/报告代际重开和 take-latest，未知值保持未知且不读取错误正文。根复核发现 report key 曾包含本地 `receivedAt`、导致同一后端 generation 仅因稍后收到就重复弹出，已用红测改为只绑定稳定后端 generation。operations 聚焦 13/13、相关 Node 26/26、ESLint 与隔离构建通过；无真实移动设备、浏览器、键盘或读屏验收，故仍为 `PARTIAL`。
- 模型保障清单已增加 dataset version、label schema、annotation/provenance、整体与分层 cohort 及 calibration bin 的 baseline 兼容性校验，显式区分 `human_gold/silver/synthetic/unreviewed`、holdout partition/access 和开发数据摘要，重叠直接拒绝。任一祖先 review 缺失、未批准或过期会动态传播到 list/status/latest exact-match，新后代提交时也立即 blocked；账本时间不允许回退、服务时钟落后高水位时 fail closed，`review_id` 单次使用阻止直接重放，前后端拒绝重复 JSON key。独立任务组合为 Python 79/79（1 条既有 warning）、Node 8/8、ESLint 与隔离生产构建通过；根代理交叉组合为 Python 55/55（1 warning）、Node 12/12。这些仍是 `manifest_only`，未读取真实 gold/holdout bytes、运行模型或核验独立 review artifact；换 ID 的伪证据仍不能由代码证明真实绑定。
- Prompt/互动引用 WIP 已完成代码收口和根代理二次核验：6 个 checked-in prompt 固定 ID/semver/spec/output/bundle SHA、变量白名单和模型参数策略；互动结果只认本轮成功工具的 `GM-T-*`，无证据、越界 ID、数字脚注、raw HTML/Markdown 图片、超长、截断、provider 异常和断流均在完整缓冲后替换为服务端 `[GM-UNKNOWN]`，不下发/不落库部分正文或异常详情。legacy runner 不再自动补 `[1]`，改用显式 `GM-Rxx`；互动 `GM-T` 与定时报告 `GM-S` 隔离。根代理扩审又以 5 组失败用例让遗留 `api_keys` 的重复 key、`NaN/Infinity`、指数溢出和过深 JSON 整体 fail closed；最新 prompt/config Python 59/59（2 条既有 warning）、architecture/registry 33/33、Vue 22/22。它们只证明来源 ID/语法/完整性边界，不验证来源真实、事实正确或语义蕴含，runtime model attestation 仍 `not_available`。
- 四个官方数据连接器的 bounded JSON transport 又以 7 组新失败样例关闭 `NaN/Infinity/-Infinity`、指数溢出为无穷大、过深 JSON 和 URL 中 control/backslash 的 fail-open；重复 key 继续在 transport 层拒绝，不发出不安全 URL 请求。连接器专项 17/17（1 条既有 warning）；未访问外站，因此仍不证明 live/许可/持续可用。
- 金融仪表盘共享缓存以失败测试证明原实现会读取 symlink/hardlink、接受重复 key/非有限值/超远未来 expiry，并可经固定 `.tmp` 或父目录 symlink 覆盖/创建仓库外目标。现改为父目录 descriptor 相对读写、`O_NOFOLLOW`、单链普通文件、4 MiB 上限、严格 JSON、最大 24h TTL 和随机独占临时文件原子替换，且词法上拒绝 release root。金融 trust 专项 22/22；未操作实际缓存或服务。
- 可靠性/health/SLO 复核已关闭 21 组失败：monitor full/fast、heartbeat summary 与 history GET 均零写；heartbeat/history 拒绝重复 key、非有限值、类型混淆、symlink/hardlink、宽权限、越界/release 路径和非单调/回退时钟；未知在线数、进度、CPU/内存、KPI 与趋势保持 `null`/`—`/neutral，曲线不跨 gap。公开 freshness 的 cutoff/lag 必须可互算，未来或过期证据降级，内部 threshold 固定不是获批 SLO；history 明示 `collection_state=not_configured`。独立组合 Python 110/110（3 warning）、architecture/registry/candidate 113/113、Node 24/24、ESLint 与 3m09s 生产构建通过；根代理交叉 Python 162/162（3 warning）、Node 24/24。没有获批 SLO、collector、incident timeline、真实流量或候选/浏览器证据。
- FR-11 增加只读维护事件投影：严格 JSON、64 KiB/100 事件、单链普通文件、读中竞态、未来/倒序/回退时间均 fail closed；未配置账本与验证空账本分开显示，GET 不写入。FR-02 同步增加公开降级处置合同：仅从实际 `down` 能力产生 `action_required`，负责人、恢复预计、最近更新时间固定不可用，未批准目标不能被写成 SLA 违约。根复核相关 Python 49/49、Node 16/16，独立合流 Vue feature 234/234；仍无正式 incident owner、ETA 来源、SLO、事件写入/审批、保留、订阅或候选浏览器证据。
- EV-01 的受界、只读、metadata-only 覆盖检查器枚举 7 个公开衍生产出/28 项能力，findings 已从 11→2→0。研究 reviewed-draft、Story Graph、金融、舆情及助手表面都有稳定 claim identity、locator/reason/unknown 边界；0 finding 只表示登记的静态能力探针通过，检查器不读取生成正文，也不验证来源、语义蕴含或事实。
- 互动助手四个实际 finalize 出口与定时报表保存前路径现均强制 `globemind.generated-claims.v1`：模型显式拆分 claim，服务端生成 `GM-C-*`，逐项只接受本轮 `GM-T-*`、冻结 `GM-Sxx` 或 unknown；问候/操作说明可标 `non_factual`，非 JSON/越界/非法处置均 fail closed。statement 正文不进入 assurance metadata；定时报表只保存服务端渲染后的 review-required 草稿。模型切分完整性、来源真实、语义蕴含、事实和人工批准仍固定未验证，因此 EV-01/AI-11 保持 `PARTIAL`。
- FR-06/AI-04 金融方法卡已精确披露当前数组位置对齐、空/单值填充、组件顺序、权重和非均匀测试向量，前后端必须逐项一致才允许通过门禁；量纲、真实时间频率、标准化、修订、基线和阈值均明确未验证/未批准，衍生值继续抑制。根扩展金融组合 73/73（3 条既有 warning）、Node 13/13 与 TypeScript typecheck 通过；这只是现状算法透明，不证明方法科学有效。
- FR-07 增加 `financial-short-sample-trend-v1` 和未批准方法卡：每个 IDX 只按同一 snapshot/cutoff 披露最多 4096 个“提供的序列点”，明确这些点未验证为独立样本；基期、最低样本量、统计方法、置信水平/区间和异常值政策均保持 null/未建立，精确 `change_pct/points` 继续抑制。根二审复现前端可接受两处同报 count=2、实际 points=1，现客户端从 exact `{time,value}` 有界数组复算后再比对；Python 101/101（1 条既有 warning）、financial Node 15/15、typecheck/build 通过。它仍不覆盖 watchlist/alert 等所有趋势入口，也不证明任何统计方法或阈值获批。
- “我的收录”页面新增 take-latest 代际门禁，刷新、认证变化或组件离开后的旧请求不再覆盖新状态；加载开始立即清旧数据，最终原子发布数据/降级提示/loading。搜索框补 label/id/name，loading/fallback/empty/无筛选结果可播报，并补 44px、focus-visible、长文本换行和 reduced-motion。Node 5/5、聚焦 ESLint 与 SFC 编译通过；无真实浏览器/设备/屏幕阅读器，不声称 WCAG 通过。
- 助手账号默认工作区初始化以失败测试关闭多条文件系统越界：锁、marker、metadata、预设工作区/知识库及其默认子目录的 symlink/hardlink 不再被跟随，用户名 `.` 不再把共享 workspace 根当成租户目录；marker version 要求精确整数，既有 JSON 有 64 KiB、单链普通文件、重复 key/非有限值/读中变化校验，新 JSON 用父目录 descriptor、`O_NOFOLLOW|O_EXCL`、随机临时文件和 no-replace hardlink 原子写入并固定 `0600`。继续扩到日常工作区/知识库入口后，用户锁及锁目录链接不再触碰外部文件，同根内用户目录 symlink 也不能跨租户；列表、预览、单文件下载和 ZIP 不再跟随或读取 hardlink，文本预览上限 1 MiB，ZIP 显式/全量清单均最多 500 项且拒绝重复，读取错误不再回显异常正文。defaults 专项 21/21，workspace 专项扩到 33/33；只在 pytest 临时目录验证，未触碰真实 workspace、账号或服务。
- 研究工作流以失败测试增加项目整态 SHA-256 封印、change/audit 双哈希链与 canonical UTC 单调时间校验；锁目录/文件拒绝 symlink/hardlink/inode 替换，写接口在持久化前拒绝重复 key 和非有限 JSON，source URL 拒绝凭据、敏感 query/fragment、control/backslash，隐私导出识别复合密钥名。artifact contract 升到 v2，JSON/Markdown/HTML/CSV 均带醒目 `reviewed_draft`、`not_for_publication`，文件名、三项响应头及前端下载适配器 fail closed；根代理研究/快照/隐私组合 73/73（3 条既有 warning）、Node 13/13。哈希仅证明本地一致性，不是签名/WORM/不可抵赖；旧未封印 v1 文件会 fail closed，尚无迁移流程，也无真实研究员、法务或来源真实性验收。
- 证据账本/图谱独立二审以 13 类失败先行用例关闭严格 JSON、symlink/hardlink/锁、revision 身份、未来/回退时间、review snapshot 错绑和原始输入数上限绕过；snapshot/event/review 增 record hash 与前序链，任何新 append 前复验完整 snapshot 绑定。来源 URL 仅保留无凭据的安全 HTTP(S) locator，StoryGraph evidence 要求恰好一个 target，非有限/布尔计数与 DB 异常正文 fail closed；前端迟到响应、`null/false/blank→0`、synthetic 布局线伪装证据及“报道数=事实已核验”的问题也已修复。独立组合 Python 77/77（3 条既有 warning）、Node 22/22；根代理交叉 Python 50/50（3 warning）、Node 17/17。SHA-256 无密钥，不是 WORM/HMAC/签名；未验来源事实、语义蕴含、因果、许可、真实 DB/账本/浏览器/负载。
- 助手的站点/成员目录读取原会在 GET 缺失时物化演示数据，且可读取越界、symlink/hardlink、重复 key、非有限/过深/超限 JSON。现已改为零写、单链 descriptor 严格有界读，并删除无调用的整套演示种子写入能力；前端 `null` 保持 `—`，不回显底层错误，将“采集中/在线/上次采集”改为明确的配置/目录标记，不冒充实时观测。file-store + workspace 根代理组合 45/45（1 条既有 warning），Assistant Node 23/23 与聚焦 ESLint 通过；未读真实目录或运行服务。
- 实体治理独立复核已收口 ledger 父路径链接/过深 JSON、6 个 mutation route 的重复 key/非有限/超限正文、搜索实体目录的 descriptor 有界读以及稳定 ID、BCP47、alias kind/type/冲突、未来/不安全审批证据和 accuracy 过度声明。前端严格复核 decision evidence、approved aliases 和 merge/split/canonical 一致性，不回显非结构化服务异常。独立组合 Python 224/224（3 条既有 warning）、Node 9/9、聚焦 ESLint 及两个生产 build 通过；根交叉 entity/search/architecture/registry/candidate 为 213/213（3 warning）。根代理又以 1 个后端和 2 个前端红灯固定“审批到期复核尚未配置”；status/catalog/entity/relations 读模型升到 v2 并必须披露 `review_expiry_policy=not_configured`，候选验证器也同步 fail closed，但没有擅自设定到期天数或声称已强制过期。该增量 entity + candidate/browser Python 148/148（2 warning）、Node 9/9。21 个种子仍全为 `review_required`，accuracy 仍 `not_measured`；无母语审阅、消歧 gold、证据真实/许可/reviewer 身份或候选验收。
- 信息架构/公开入口复核已完成源码级收口：legacy 深链改为可聚焦迁移说明，认证回跳只接受真实内部非循环路由，preload 支持去重/取消迟到/失败重试，未登录导航不展示私有入口；canonical/sitemap/robots、CSP 前置与静态活动文档产物排除形成一致契约，外链和 `postMessage` fail closed。独立合流后定向 Node 79/79、全 feature 204/204、聚焦 ESLint、`git diff --check` 与隔离 build（4277 modules）通过；只有既有大 chunk warning。未跑真实浏览器、键盘、读屏、爬虫或线上响应头/MIME/nosniff，因此不声称真实可访问性或线上 CSP 验收。
- CD-01 新增国家档案 schema-only catalog：`GET /api/authoritative-data/country-profiles/catalog` 固定 `available=false`、`operational_state=not_configured`、`live_checked=false`、`implementation_scope=schema_catalog_only`、`profiles=[]`，只登记 10 个 section/32 个字段及来源、许可、owner/review/expiry 最低证据，不读取数据或网络。前端 `/country-profiles` 只展示版本化结构、缺失原因与 fail-closed 门禁，严格有界读、拒绝 schema 膨胀/事实形状、隔离迟到响应并保持 noindex；桌面/移动导航均可达。后端聚焦 20/20、authoritative/architecture/registry/candidate 133/133（1 条既有 warning），前端/IA 根交叉 28/28、ESLint 与隔离 build 通过；这不等于存在任一国家档案，试点国家、事实、owner/reviewer、母语验收仍缺。
- 新闻翻译边界以失败用例确认旧端点无需认证、可接收任意目标/模型、供应端正文/地址可泄漏且无可核对 provenance；供应端又有 5 类红灯会接受重复 key、`NaN`、指数溢出、超过 128 KiB 和错误媒体类型。现端点只允许认证用户、`zh-Hans`、受界文本及显式源语言，供应端只允许无凭据的 literal loopback HTTP(S)，响应按解压后字节流限量并严格 JSON；返回内容最小化 receipt，含源文 SHA-256/长度、模型 ID、`not_reviewed/not_measured/not_configured`，不含源文或 endpoint。前端独立重算源文 SHA-256、限制 100 段/6 万字符、take-latest + abort，并将机器译文和数据库旧译文分别披露为“未经人工复核/质量未测量”和“provenance 未登记”。后续敌手复核又先得到 backend 9 个、frontend 4 个及迟到失败批次 1 个红灯，关闭源文空白漂移、loopback URL 混淆、provider model/Content-Length/Unicode 绕过、前端重复 key/伪 receipt 和失败 worker 迟到写；最新 dashboard 29/29、相关 Python 96/96（3 条既有 warning）、translation Node 9/9、同页组合 28/28、聚焦 ESLint 与隔离 build 通过。未调用真实翻译模型，不证明译文准确、术语一致或服务可用。
- WF-12 收藏工作流新增认证 `POST /api/user/favorites/batch`：1–100 个显式 set 操作、重复项拒绝、expected revision 乐观冲突、单事务/行锁和幂等重放；所有收藏 mutation 在写前拒绝超过 64 KiB、过深、重复 key、非有限数和错误媒体类型。GET 升为 `user-favorites-v2`，warning 不再混进收藏 ID，前端按认证代际 take-latest、严格限制响应并只做显示层 ID 去重，不声称集合自动同步。失败先行 Python 18 个红灯、Node 2 个红灯后，专项 Python 18/18、交叉 111/111（3 条既有 warning）、相关 Node 49/49、聚焦 ESLint 通过。标签规范、智能文件夹、集合合并、完整批量 UI、真实 DB 竞争和浏览器验收仍缺。
- WF-13 定时简报安全复核从 20/20 红灯收口到后端组合 147/147（3 条既有 warning）、前端 28/28、聚焦 ESLint和隔离 build 通过。GET 不再创建目录/锁或重写状态；descriptor-bound/no-follow/单链/0600、原子受界严格 JSON、租户 owner 绑定、未来时间与时钟回退拒绝、队列去重和安全错误码已覆盖，前端隔离身份代际并诚实显示 null/stale。它仍是单机可变 JSON 状态，没有分布式 fencing、事务性 exactly-once、append-only/WORM、跨文件 schedule/report 原子事务或真实通知 runner。
- WF-14 新增管理员只读 `GET /api/governance/api-contract` 与精确 canonical `openapi.json` 下载，从当前运行应用 OpenAPI 受界重算 path/operation 数与 SHA-256，并列出源码默认限流、429/`Retry-After` 语义和安全 Bearer 占位示例；实际有效限流证明、版本/弃用/兼容承诺、交互文档和多实例协调全部固定为 `not_available/not_configured/not_approved`。敌手复核先后复现 operation ID 重复/缺失、重复 JSON key、伪造 path 计数、过深/过大/非有限 schema、生成异常和下载哈希错绑；现限制 4 MiB/5000 paths/40000 operations/64 层/250000 节点，成功与 503 均 `no-store/nosniff`，七个代理 method 使用独立 operation ID。根合流 WF-14/auth/http/architecture/registry 93/93（7 条既有 warning）；未发布公共 API 产品、未观测运行限流、未承诺支持 SLA。
- FR-05 来源贡献扩审以 backend 3 个、frontend 1 个红灯证明重复/空 ID、布尔记录数和自报覆盖可绕过信任门禁。现最多 128 条来源，稳定 ID、正整数观测数、逐项 `usable/not_usable` 与安全原因码、分母、可用/不可用 ID 清单由后端重算且前端与实际行逐项核对；方法明确 `source_weighting=not_established`、`availability_gate_only_not_numeric_attribution`。相关 Python 196/196（3 条既有 warning）、金融 Node 11/11、typecheck/build 通过；没有许可、真实运行状态、批准权重或数值贡献，不能声称指数方法完成。
- ML-07/ML-09 新增 geography semantics v1：只做 ISO/GeoNames/Wikidata/稳定实体 URN 的语法边界、成对有限坐标与显式精度/不确定度约束，并强制 source/audience/event/mentioned 四角色不合并、不跨角色推断。首轮 24 个红灯后专项 34/34、交叉 Python 185/185（3 条既有 warning）；它没有权威映射、许可、准确率、人审、回填或索引/API/UI 接入，因此只记 `PARTIAL`。
- AI-12 纠错治理先后得到后端 9+9 个和前端 3 个红灯；`POST /api/opinion/feedback` 现用 4 KiB/32 nodes/depth 4 的严格 JSON，必填 `quality_correction`、训练不同意/退出，拒绝 extra/自由文本/自报批准，仅存 news ID 与结构化 correction。未审阅记录不再影响评分、隐藏、gold 或训练，训练 export gate 无条件拒绝；成功/422/503 均 no-store 且不回显异常。专项 20/20、相关 Python 316/316（3 条既有 warning）、Node 24/24、ESLint/build 通过；历史数据处置、保留删除、人审账本、脱敏、lineage 和法务/隐私/模型 owner 均未配置。
- IA-08/IA-09 增加两层诚实产品地图：首页三项已绑定模块通过后端 generation/cutoff/scope/method 与前端 exact projection 复核，未绑定 Agent 卡保持 `not_configured`；公开帮助页新增 `product-data-flow-v1`，只列 8 个当前模块和 9 条用户显式交接，每条均固定 `automatic=false`、`truth_assurance=not_established`，逐模块披露输入、输出、状态和非等价边界。IA 聚焦 Node 11/11、全量 Vue feature 255/255（合流前）、实际声明扫描 0 findings；它不是运行 lineage、数据真值/完整覆盖证明，也未经产品/数据 owner 或真实浏览器验收，故两项仍为 `PARTIAL`。
- SR-04 的已知公开入口已统一到 `search-mode-semantics-v1`：exact 改称“全部词”，明确未引号 token AND；fuzzy 改称“主题扩展”，明确未写运算符时 topic OR、版本目录叶节点扩展且不是向量相似度；显式 Boolean 与双引号原样短语在两模式保持一致。搜索页、六个预设和助手工具详情共用中立 governance contract，曾因 Assistant→Search→Assistant 依赖环触发 feature gate 红灯，改为中立模块后 Node 49/49、后端搜索/architecture/registry 88/88（2 条既有 warning）、feature/import gate、ESLint、声明扫描和隔离 build（4293 modules）通过。真实多语言 token 边界、全部 API 客户端、qrels/召回与浏览器未验证，故保持 `PARTIAL`。
- AI-05 新增 `opinion-three-axis-method-v1`：响应投影层把目标立场、文本语气、现实影响分为独立结构，数字字符串/空白/bool、伪 source field/scale/model 和自报方法卡均 fail closed；当前只有受 trust/raw-field 绑定的立场可用，语气与现实影响固定 unknown，UI 明示合同范围只是 `response_projection`。独立组合 Python 80/80（3 条既有 warning）、Node 24/24、全量 Vue ESLint、build（4293 modules）、声明扫描与 diff-check 通过。v6 上游仍含 tone→stance 启发式，真实标注、独立 tone/impact 模型、轴独立性和事实验证未建立，因此仍为 `PARTIAL`。
- EG-03 核心响应现统一 `graph-sampling-provenance-v1`：requested/evaluated/returned/excluded 只接受严格已知计数或显式 unknown，`complete_graph_claim=false`，不披露 excluded IDs；Graph Briefing 多路径、Story Graph L2/L3/legacy 与 Ground News timeline 均有固定 limit/reason。二审又关闭缺失计数 `None→0`、分页窗口误称“排除/截断”和实际 timeline 消费者的 `null→0`/强关系文案；关系/Ground/mobile 合流 Python 151/151（1 条既有 warning）、Node 42/42。没有真实 DB 全集、历史 schema 回放或候选覆盖损失观测，故保持 `PARTIAL`。
- SR-09 新增 `search-hit-display-v1`：后端只返回当前新闻结果 title/abstract 中正向原样词项的 Unicode code-point offset，不回传词项/HTML，不把无显示片段解释为文档未命中，也不声称别名 span、相关性分数或正文其他位置；前端 exact-key 校验后用 Vue 文本节点和 `<mark>` 渲染，重叠、越界、布尔/浮点 offset、额外字段和契约漂移均 fail closed。红灯后搜索宽组合 289/289（3 条既有 warning）、Node 29/29、全 feature 279/279、ESLint/架构/声明扫描与隔离 build（4298 modules）通过。层级/实体/事件结果、完整正文、别名证据、真实多语言数据和浏览器验收缺失，因此仍为 `PARTIAL`。
- EG-05 以封闭关系本体统一 current/legacy Story Graph 和 Ground News 公开边：parallel、时间重叠/邻接、实体重合、synthetic layout 及历史 `causal_*` 均不能升级为 influence/causal；缺失、旧版、矛盾或伪造语义降为 `relation_unknown`，影响与因果固定 `not_established`。红灯为 Python 13 项及两个缺导出 Node，用例收口后 Python 151/151、Node 42/42、ESLint/build/架构门禁通过；未做历史数据迁移、真实候选回放或人工因果 gold，故保持 `PARTIAL`。
- AI-03 增加六个模型输出面的机器清单、认证只读接口和可复制治理 UI；所有面都绑定受界源码 locator，但缺 model ID/version、部署时间、变更说明或 runtime attestation 时只显示 `unknown/not_available`，不读取环境、provider 路径、prompt、正文或 secret。后端扩展组合 100/100（3 条既有 warning）、Node 12/12、ESLint、声明扫描与隔离 build（4298 modules）通过。静态清单不证明运行实例/发布产物，也没有持续发现所有未来输出面，故保持 `PARTIAL`。
- WF-11 研究导出升级为 v3：JSON/Markdown/scriptless HTML/CSV 共用 report hash、citation ID、locator 与许可边界，提供 12 项字段 allowlist 并强制排除创建者、原始查询/筛选、source note、decision rationale 和 review comment；安全 HTTP(S) locator 也固定许可 `unknown/not_established`，四格式保留 `not_for_publication`。PDF/Word/PPT/DOCX 和未知格式在写前拒绝，客户端下载验证 hash/ETag/filename/fields/no-store/nosniff/CSP。红灯 6 项后后端宽组合 167/167、Node 14/14、全 feature 279/279 与 build 通过；无机构许可政策、正式 citation style、文档格式或研究员签发，故保持 `PARTIAL`。
- QA-13 增补身份账本未来事件/时钟回退门禁、4 KiB 严格 JSON 写边界和成功/失败 no-store；前端以一个 exact contract 原子核对 MFA、tracked session 与脱敏 audit，并把 SSO、安全密钥、受信设备、runtime IdP attestation 和独立安全复核分别标为未配置/不可用/未提供。红灯覆盖 2 个账本时钟、能力清单缺失、原始 JSON/Content-Length/Pydantic 回显与前端矛盾状态；锁定 Python 身份/架构/候选组合 240/240（3 条既有 warning）、Node 身份/隐私 14/14、ESLint 通过。未接企业 IdP/WebAuthn/设备信任，也无浏览器、渗透或候选证据，故保持 `PARTIAL`。
- SR-10 新增内容无关的 `research-saved-search-monitoring-v1` 只读投影：只输出保存检索 ID、查询合同 SHA-256 与 linked snapshot 状态，不返回名称、查询、筛选或 snapshot ID；scheduler、checkpoint、差异语义、仅看新增和通知一律固定为未配置/未建立/不可计算，成功与 503 均 no-store。红灯从后端缺 facade 导出、前端缺 schema 导出以及 503 缓存头缺失开始，当前研究组合 Python 52/52（3 条既有 warning）、Node 17/17、架构/registry/candidate 125/125。它只证明不会把保存检索冒充持续监测，真实后台运行仍不存在，故保持 `PARTIAL`。
- EG-11 建立 16 项公开图指标 inventory 和中央客户端方法卡：已知 Story Graph、Ground News、Graph Briefing 与 DataSearch 显示面的研究价值、关系强度、链质量、Blindspot、排序/覆盖分和边权重都降为 unknown 或 layout-only，可展开现状公式、输入状态与证据缺口；Graph Briefing 旧聚合 DTO 也不再直接返回精确值。红灯为后端 4 项与前端缺中央模块，收口后图域 Python 143/143（1 条既有 warning）、前端图/Ground/Search 74/74、全 feature 273/273、ESLint/build/声明扫描通过。兼容 API 的内部排序旁路、完整运行面自动发现、批准方法/阈值和真实证据 locator 仍缺，故保持 `PARTIAL`。
- CD-02 新增空事实、schema-only 的 `globemind.country-institution-governance.v1`：4 个固定 section、27 个固定字段分别约束宪制、法定/实际权力、行政体系和证据治理，所有未来事实必须带 citation、时态、许可、owner/reviewer，且法定与观察事实不得合并推断；公开 GET 固定 `available=false`、`facts=[]`、`live_checked=false`。红灯为缺 contract 导入，收口后 CD-01/02 23/23、authoritative/architecture/registry/candidate/auth 170/170、声明扫描 0 findings。没有任何国家事实、试点/法源/许可/母语研究员，故只从未核验推进到 `PARTIAL`。
- AI-11 新增内容无关的三表面离线评测合同：互动助手与定时报告仍是自由 Markdown，逐 claim coverage 固定 unknown；研究导出仅接受真实 v3 形状的 hash claim/citation ID、statement SHA-256、支持/反方绑定和 unresolved-gap disposition，并且只计算 manifest 自报的句法处置率。独立二审先撤回旧绿灯，再关闭缺 observation 伪 0、自报 fixture 冒充 passed/observed、v3 ID/binding 错配、metadata/result 矛盾及 non-stream/scheduled failure 证据夸大；缺研究 observation 现为 `not_observed` 且全部数字 `null`，总体只可为 `manifest_conforms_with_open_findings` 或 fail closed，hallucination rate 永远不可计算。最终相关 Python 110/110（3 条既有 warning）；claim coverage checker 仍按设计保留互动/定时两个 claim-ID finding。无独立 replay、真实 human gold、source truth、语义蕴含、事实核验或模型批准，故保持 `PARTIAL`。
- 本轮跨域增量新增并复核五条诚实纵切片：CD-05 在空事实公开目录之外增加严格离线 primary-document bundle intake，逐字节核验原文、条款 anchor、许可、效力期、修订图和独立审阅；Search 将 human-gold qrels 扩为真实 corpus/annotation/adjudication bundle，并用冻结 ranked-result artifact 生成哈希绑定 benchmark receipt，但仓库不附真实 qrels 或搜索运行；互动与定时助手出口已强制逐 claim JSON、服务端 ID、引用或 explicit unknown；browser smoke v2 以固定 snapshot/generated-at 覆盖五个高风险页面九项业务语义探针；continuous audit 配置每日只读发现与 30 天声明产物留存，并将九项验证器纳入手工/仓库 CI 计划。它们均不访问网络、真实数据库、候选或 release；issue integration、具名人工 owner 和真实外部验收仍未配置。
- 接续增量进一步收紧五域边界：国家法源新增经批准 pilot-plan 与 bundle 的 country/document-kind/verified-license 对账 receipt，始终 `facts_published=false`；qrels 新增全 query 精确覆盖、同 country/topic/graded-qrels 的 adjudicated translated-intent 计划及 language/country/overlap 描述性切片，parity/quality 仍 `not_established`；助手 claim ID 现绑定 statement SHA-256 与精确 source artifact SHA-256；五页九探针除文本哈希外还核验 alert/status/group、live region、atomic 与 label-presence；定时 workflow 新增 content-free triage，只保留 finding code/validator status，绝不创建 issue。仓库仍无真实 pilot plan/法源/qrels/corpus/ranked run，未跑候选浏览器或 GitHub schedule。
- 本次接续再补五项可独立复核但不夸大的证据：国家 claim plan 只保留 statement SHA 并把支持/反证绑定到已核验文档与条款 anchor，冲突、时态或许可不满足即不就绪且绝不发布；搜索只允许同 qrels/corpus/adjudication/plan/query/group 的 baseline/current 原始差值，不配置阈值、不下回归结论；外部 AI 观察重新读取精确来源工件并重算 inventory/claim ID，但不保留正文、不验证事实或蕴含；浏览器证据 verifier 对 exact report SHA、13 页 × 2 视口、5 个高风险业务页、9 个语义 selector、18 个探针观察和 26 张 PNG 重新核验，但本轮未启动浏览器；审计趋势比较器只输出 finding/validator/status 的描述性变化，自动历史基线获取仍未配置。
- 最终快速收尾未增加新功能或空 schema：补齐 AI/浏览器外部证据读取的 release 路径拒绝与读中身份复核，浏览器 strict JSON 和审计 trend strict JSON 拒绝非有限数；qrels 外部运行、切片及回归指标统一拒绝 `1e400→inf`；浏览器收据不再把内存 stub 的 evidence verification 写成候选验收通过，而是保持 `candidate_acceptance=not_established_in_memory_stubs_only`。导出 facade 已齐全，无明确死代码；四类真实输入与锁定运行时命令见最终交接清单。
- 移除首页无目录/质量证据支持的“近 300 万、100+、60+ 语种、200+ 智库、智能决策”等营销数字，改为历史资料、可信门禁、查询解释、人工复核和许可边界。

### 3.5 当前未完成的 WIP，接管后先核验

- 搜索域本轮 WIP 已完成代码和专项核验：普通查询 token 语义、预设筛选、语言显示、actor/target 字段语义和离线质量评测均有回归证据；真实 qrels、200 个专家问题、母语审阅、搜索库副本和候选负载仍为外部阻断，不能声称搜索相关性已经提升。
- 证据/证据图谱独立复核已完成代码、聚焦门禁和根代理交叉复跑；还需在全工作树最终全量回归中继续覆盖，且不将无密钥哈希写成不可篡改证据。
- 实体治理独立复核、build 和根代理交叉复跑已完成；审批到期策略只完成 `not_configured` 诚实披露，正式强制有效期、旧记录迁移和负责人仍是未完成项。
- 信息架构/QA 的源码、Node、ESLint 和隔离构建复核已完成；真实浏览器、键盘、读屏、爬虫及线上 HTTP header/MIME 验收仍为外部/环境阻断。
- 国家档案、新闻翻译、收藏批量工作流、助手定时调度和认证 API 契约目录均已完成源码/聚焦门禁收口；最终仍须跑全 Vue/全后端，且 country facts、翻译人评、集合完整 UI、真实 scheduler/通知和正式 API 产品支持均保留阻断。
- 130 项逐行矩阵已落到 `ops/audit/registry.json`，本文件记录汇总与边界；当前机器快照 source cutoff 为 2026-08-10 08:05:00 UTC。runner v2 除登记完整性、状态组合、locator、证据期限和 `automation_state` 外，会验证九项离线 validator plan、只读日程、content-free triage 和 30 天留存声明，并明确区分 Git HEAD 与 clean/dirty worktree；它不观察 CI 是否真实执行、不输出脏路径，也不计算或宣称 worktree 内容哈希。CD-05、SR-03、AI-06、AI-11、QA-01 的证据已按最终回归刷新但状态不夸大；定时发现/分诊/留存和离线趋势比较器已配置，自动历史基线获取、issue integration、具名人工 owner 与 dirty worktree finding 仍保留。

## 4. 130 项审计总分类

完整审计包含 130 项：IA-01..10、SR-01..12、FR-01..12、ML-01..10、EV-01..13、EG-01..14、CD-01..18、AI-01..12、WF-01..14、QA-01..15。

| 审计域 | 当前判断 | AI 还能完成的重点 | 必须依赖外部资源的部分 |
| --- | --- | --- | --- |
| IA 信息架构 | 大部分代码修复已落地，仍需逐路由复核 | 路由 inventory、旧深链迁移、导航可发现性、canonical/sitemap 静态检查、营销声明扫描 | 真实域名 canonical 决策、候选浏览器验收 |
| SR 搜索与可复现性 | 功能契约增强，质量未证明 | 修 422/字段错位；自然语言 query planner；词法/实体/向量融合；qrels schema；Recall/nDCG/MRR/零结果/P95；结果解释 | 200 个专家问题、人工相关性等级、真实候选数据库负载 |
| FR 新鲜度与数据质量 | trust/freshness 基础已落地，catalog 仍 blocked | 批量质量 profile、重复/漂移/截断/缺失报告、按来源切片、质量 artifact 校验、catalog fail-closed 投影 | 批准阈值、事实准确性抽检、许可和 owner |
| ML 多语言与实体 | 版本化 21 种子和治理账本已落地 | 扩大公开权威实体种子、别名冲突检测、转写规则、语言显示与查询切片评测 | 母语审阅、同名消歧金标准、≥95% 真实 benchmark |
| EV 来源与证据 | claim/paragraph/snapshot/revision 基础已落地 | 互动 AI 引用边界、引用完整性扫描、来源 profile/data card、导出证据核验 | 全文保存许可、来源事实可靠性判断、WORM/签名 |
| EG 证据图谱 | 图状态清空、迟到响应门禁和部分证据链已落地 | 全局主张 inventory、孤立节点/断链/过期证据扫描、证据类型覆盖率 | 研究员对语义蕴含、关系和主要判断的确认 |
| CD 国家数据 | 只有通用 contract，实质内容不足 | 建三国模板、字段 schema、公开源 connector、缺口报告、双语资料包生成 | 试点国家选择、母语团队、正式来源和许可 |
| AI 模型与助手 | 模型 assurance 和定时报告门禁已有基础 | prompt registry/version/hash、互动引用、注入测试、离线 eval harness、输出 schema、失败模式文档 | 真实 gold、独立红队、外部 review、模型运行审批 |
| WF 研究工作流 | 纵切片较完整，真实验收缺失 | 标注指南模板、研究任务 fixture、版本 diff、导出 validator、审阅队列与反馈统计 | 真实研究员端到端执行、机构批准、正式报告签发 |
| QA 质量/可访问性/安全 | 源码和静态测试部分完成 | 44px/label/focus/ARIA 静态扫描、错误/空/stale/offline 状态、CSP/route/secret/contract drift 检查 | 真实设备、屏幕阅读器、渗透、性能、独立 WCAG 验收 |

当前逐项登记快照（source cutoff 2026-08-10 08:05:00 UTC；逐行 title/owner/validator/evidence/blocker 见 `ops/audit/registry.json`）：

| 域 | PROVEN_CODE | OBSERVED_SAMPLE | PARTIAL | EXTERNAL_BLOCKED | NOT_STARTED_OR_UNVERIFIED | 合计 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| IA | 6 | 0 | 4 | 0 | 0 | 10 |
| SR | 5 | 0 | 7 | 0 | 0 | 12 |
| FR | 6 | 1 | 5 | 0 | 0 | 12 |
| ML | 3 | 0 | 5 | 2 | 0 | 10 |
| EV | 2 | 0 | 7 | 4 | 0 | 13 |
| EG | 7 | 0 | 6 | 1 | 0 | 14 |
| CD | 0 | 0 | 10 | 0 | 8 | 18 |
| AI | 1 | 0 | 9 | 2 | 0 | 12 |
| WF | 8 | 0 | 6 | 0 | 0 | 14 |
| QA | 10 | 0 | 4 | 1 | 0 | 15 |
| **总计** | **48** | **1** | **63** | **10** | **8** | **130** |

逐项矩阵必须使用以下五种状态，禁止只写 DONE/WIP：

- `PROVEN_CODE`：当前源码和相称测试证明代码行为。
- `OBSERVED_SAMPLE`：只在明确样本/离线 fixture 上观测，不可外推。
- `PARTIAL`：只覆盖审计项的一部分。
- `EXTERNAL_BLOCKED`：需要人、许可、设备、候选或基础设施。
- `NOT_STARTED_OR_UNVERIFIED`：未实现或当前证据不足。

## 5. 大版本路线

### V0.9 “可信止血版”

目标：停止虚假实时、过期精确分数、隐式 mock、测试页面泄漏、误导路由和无基本治理入口。

当前：主要代码已落地；仍需本轮逐项矩阵、全量回归和真实候选/浏览器门禁。任何缺 cutoff/coverage/source/method 的关键值必须隐藏或不可计算。

### V1.0 “可信数据基础版”

目标：数据目录、数据卡、模型卡、许可、owner、schema、quality、provenance、freshness 和 SLO 形成统一 release gate。

当前：骨架和四源 connector 已有，但 9/9 catalog 记录 blocked。V1.0 只有在全部关键记录 eligible、真实质量证据可复算、批准的 SLO 与候选观测存在时才能通过。

### V1.5 “研究可复现版”

目标：三个试点国家、约 200 个专业问题、保存检索、证据/反证/缺口、判断、人工审阅批准和确定性报告。

当前：工作台代码纵切片较完整；三国数据、专家问题、qrels、研究员验收和正式报告仍缺。搜索质量与 prompt/citation 是此版本的首要技术任务。

### V2 “机构协作与专业数据版”

目标：跨语言实体治理、团队 ACL/SSO/租户、专业授权源、原文快照与修订、隐私权利、模型漂移、容灾和事件响应。

当前：多个本地基础纵切片已有；企业 IdP、租户、授权源、对象存储/WORM、多区、值班与真实演练需要组织资源。

### V3 “专业情报研究版”

目标：10–20 国本地语言体系，海运/航空/贸易/制裁/供应链/遥感等专业数据，关系网络、情景与判断变化、回测校准和独立红队。

当前：只能建设通用 contract、connector SDK、评测和治理工具；没有数据、专家、许可与历史真值时不得恢复“实时情报”或综合风险指数营销定位。

### VNext “持续自我发现与改进版”

目标：把审计从一次性人工活动变成版本化、只读、可定时运行的工程能力。

最低组成：

1. 机器可读 audit registry：130 项 ID、owner role、严重度、验证器、证据、状态和外部 blocker。
2. `continuous_audit` 只读 runner：汇总 route/contract/schema/import/env/secret/marketing-claim/a11y/search-eval/data-quality/prompt/version/test drift。
3. 每次输出不可覆盖 JSON + Markdown 报告，带代码 SHA、方法版本、截止时间和失败原因；不得包含 secret、正文或个人信息。
4. CI/定时任务只负责发现和创建 issue/artifact；自动补丁只能进入隔离分支并跑测试，禁止自动部署、删数据、改生产配置或调用外部付费源。
5. 趋势比较：新增问题、复发问题、已解决问题、证据过期、测试覆盖下降、搜索/数据质量退化。
6. 无 runner、无调度器、无 artifact retention 时必须显示 `automation_state=not_configured`，不能声称“系统会自动优化”。

当前：registry 与 v2 只读 registry runner 已实现；它会核验登记完整性、状态组合、locator、证据期限、脏工作树边界，以及九项跨域离线 validator plan 的 schema/locator/全 false 权限和仓库 workflow 声明。独立 runner 只按 `kind+locator` 派生经验证的绝对 Python runtime 命令，以最小环境执行九项离线验证，stdout/stderr 仅保留有界哈希，并以 no-replace 方式写入仓库/release 外目录。仓库只读 workflow 已声明每日调度、content-free triage 与 30 天指定产物留存，故 `automation_state=configured_discovery_only`；triage 只保留 finding code/validator status，绝不创建 issue 或外发消息。离线 trend comparator 可对两份 exact-hash triage 收据输出新增/解决/持续 finding、validator 退化/恢复/范围变化和登记状态差；最终以 07:14 与 08:05 两份外置收据实跑，只有 dirty finding 持续、九项 validator 均保持 passed、五类登记状态 delta 均为 0。workflow 尚未配置历史 artifact 自动获取，因此真实 CI 执行观测与自动趋势基线仍不可声称。issue integration、已完成人工 triage 和具名 owner 同样未配置，手工通过也不表示候选或生产验收。

## 6. 12 小时执行工作池

采用“广度轮转”，每 45–90 分钟换一个域；同一域最多连续两轮，除非仍有用户可见 P0。

### A. 事实、时间与产品诚实性

- 完成首页/导航/帮助/详情/导出/空状态的声明 inventory。
- 建立无证据营销声明静态检查：数字覆盖、实时、准确率、可信、专业结论、语种/来源数必须有 catalog locator 或改为限定文案。
- 修复所有 family/initiator、source/type、today/latest、cached/live 等字段语义错位。
- 所有 `null` 保持不可用，禁止前端把它转为 0。

### B. 搜索质量

- 收口当前 WIP：预设 422、跨类型残留筛选、L3 initiator 错位、排序/相关性标签。
- 建立 `search-eval-v1`：query、language、country、topic、intent、qrels、provenance、review state。
- 实现 Recall@k、nDCG@k、MRR、P@k、zero-result rate、timeout/P50/P95；silver 与 human gold 分开。
- 建立一批不冒充专家金标准的 adversarial/silver 查询：中文一句话、英文、短语、排除、时间、实体别名、同名歧义、无结果、超限。
- 设计并实现受约束 query planner：原句 → 关键词/短语/排除词/实体/时间字段；显示 requested/applied semantics，可关闭，可回退，不静默改写。
- 评估 lexical + entity + fulltext + vector 的 RRF/重排，但没有同 corpus qrels 时只记录实验，不宣称质量提升。

### C. 数据质量

- 完成当前 profiler 的 CLI/contract/文档审阅，并补 invalid URL、输入边界、no-replace、内容不泄漏测试。
- 增加按来源/语言/日期的聚合切片、near-duplicate 候选、schema drift、volume interruption 和 extraction fidelity 指标；避免 O(n²) 无界计算。
- 设计 baseline/current 比较 artifact；无批准阈值固定 `not_computable`。
- 对多个仓库内小样本运行只读 profile，明确样本名、条数、cutoff 和局限；不连生产 DB。
- 将经验证且未截断的 artifact 以 fail-closed 方式投影到 data catalog；损坏/过期/方法不匹配保持 unknown。

### D. Prompt、AI 与引用

- 建 prompt registry：prompt ID/version/SHA、模板变量白名单、model/temperature/max tokens、output schema。
- 交互助手只可引用本轮真实工具结果；无证据写 unknown，不自动补 `[1]`。
- 清理 legacy `research_agent.py` 的默认伪引用逻辑。
- 增加 prompt injection、越界 citation、raw HTML/image、敏感字段、超长输入、模型异常和流式中断测试。
- 生成物 metadata 不记录用户正文、密钥、base URL 或 provider secret；hash 只作写入时指纹，不冒充读取验链/WORM。

### E. 更优质来源

- 复核现有 World Bank、IMF、UN SDG、Crossref adapter 与许可表述。
- 优先研究官方/研究型源的安全 connector：UCDP、ReliefWeb、HDX HAPI、GDELT、UN Comtrade、WTO、UN/OFAC/UK/EU sanctions、OpenAlex、DataCite。
- 每个 connector 必须有官方文档、host allowlist、HTTPS、禁重定向/环境代理、有界 timeout/解压/记录、license evidence、cutoff、coverage、payload hash、失败不回旧记录。
- 只登记不等于 live；需要账号/token/appname/EULA/商业许可的源保持 blocked。
- Reuters/AP/ACLED 等需要商业或专门许可的源只进入资源申请清单，不擅自抓取。

### F. 研究工作流与反馈闭环

- 建 200 问题模板、标注指南、相关性等级、冲突裁决和一致性统计工具。
- 建搜索结果反馈 contract：相关/不相关/缺失/重复/时间错误/实体错误，主体隔离、最小化、可导出，不自动当 gold。
- 建研究任务离线 fixture，覆盖证据、反证、unknown、review reject、version diff 和导出完整性。
- 互动聊天和 Agent 产出逐步接入同一来源边界；仍需人工最终批准。

### G. UX、移动端与无障碍

- 静态复核所有表单 label/id、H1、aria-live、focus、键盘操作、44×44、移动遮挡、横向溢出。
- 复核 loading/empty/error/stale/offline/timeout/unauthorized 各状态是否原子清旧数据。
- 搜索和图谱提供键盘/列表替代；图画布不可成为唯一信息通道。
- 无浏览器/设备工具时只能标源码 QA，不能声称真实 WCAG/移动验收。

### H. 安全、隐私与运行

- 复核 canonical `user_id`、admin 边界、redirect fail closed、CSP、static fallback、敏感字段和存储 root。
- 完善 candidate validator 的精确 schema/ID/敏感字段检查；FakeClient 仍只算验证器测试。
- 复核 MFA/session/privacy/research/model/service/triage 账本的 zero-write GET、symlink/hardlink、duplicate JSON key、hash chain、size/count limits。
- 不执行真实删除、alert mutation、服务切换或候选 smoke。

### I. 自动发现机制

- 创建机器可读 audit registry 和只读 runner。
- 建 claim linter、contract drift、route inventory、catalog evidence expiry、test evidence staleness 检查。
- 生成 backlog 时按 `severity × user impact × confidence ÷ effort` 排序，并强制跨域轮转。
- 每个自动建议必须带：发现证据、影响、拟改范围、测试、回滚、外部权限；缺一项不自动改。

## 7. 自动发现与自动优化的安全保证

“任务结束后依旧自动优化”必须拆成两层：

### 可安全自动化

- 定时只读扫描、质量评测、契约/路由/文案/测试漂移检测。
- 生成不可覆盖报告、issue 或隔离分支补丁。
- 在临时/CI 环境运行离线单测、lint、类型检查和构建。
- 根据证据更新机器 backlog，但不得自动把 unknown 改为 passed。

### 必须人工批准

- 合并代码、发布、重启服务、迁移数据库、执行删除、改权限、轮换密钥。
- 访问生产/候选、付费 API、商业数据、个人信息或受许可全文。
- 认定事实正确、来源可靠、模型达到专业标准、SLO 获批或 WCAG/渗透通过。

因此，仓库可实现并验证“自动发现 + 生成安全候选改进”；要让它在会话结束后真正定时运行，还需要实验室提供 CI runner/cron、artifact 存储、issue token 和责任人。未配置这些资源前，状态必须是 `not_configured`，不能虚假保证后台仍在运行。

## 8. 验收方法

每项工作必须记录四类证据：

1. 代码 locator：具体文件和符号/路由。
2. 行为证据：新增失败用例先证明缺口，修复后通过。
3. 回归范围：聚焦、相关组合、全量分别是什么，不能用小测试支持大结论。
4. 诚实限制：哪些只在 fixture/sample 上验证，哪些仍需人/候选/外部源。

最低最终门禁：

- `git diff --check`。
- 锁定 Python runtime 的新增专项、相关组合、architecture/registry/candidate validator，最后尽可能全后端。
- Vue 全 feature、全 ESLint、SFC/生产 build。
- 金融 Node/typecheck/build（若金融域有改动）。
- route/static/CSP/secret/runtime manifest/import boundary。
- 所有失败分类为本轮回归、并发 WIP、环境缺失或既有问题；不得静默忽略。
- 满 12 小时时间门槛，并完成 130 项矩阵与 AI 可实施清单；否则目标保持 active。

## 9. 资源申请清单

向导师/实验室应申请：

- 人：三国母语研究员、数据 owner、模型 owner、隐私/安全负责人、法务、SRE/值班、独立标注与红队。
- 数据：UCDP/ReliefWeb/HDX/GDELT 等公开研究源接入审批；ACLED、Reuters、AP 和专业贸易/制裁/海运/航空数据的许可预算。
- 计算：隔离 CI runner、搜索评测数据库副本、向量检索节点、对象存储、artifact retention；不可直接拿生产库做实验。
- 运营：候选环境、受控测试账号、监控/告警、备份恢复、多区/容灾、批准的 SLO 和演练窗口。
- 治理：IP/数据/发表/商业边界协议，来源许可登记，标注手册，模型审阅和正式签发流程。
- 测试：真实桌面/移动浏览器、屏幕阅读器、授权渗透测试、负载测试和 AI 红队。

## 10. 连续执行日志

| UTC 时间 | 工作域 | 发现/变更 | 验证 | 尚存风险 |
| --- | --- | --- | --- | --- |
| 2026-08-09 16:04 | 总控 | 创建至少 12 小时持续目标，重读 handoff 与完整审计 | 目标 active | 130 项逐项矩阵未完成 |
| 2026-08-09 16:08 | 数据质量 | 新增有界、内容不落盘、无 release 决策的新闻质量 profiler/CLI | Python 15 passed | 只测机械规则，无近重复/事实/gold/批准阈值 |
| 2026-08-09 16:11 | 数据样本 | 只读观测 358 条 Wave1 样本，67 条机械问题，cutoff 2026-06-21 | no-replace JSON artifact 写入隔离 `/tmp` | 不能外推全库；临时 artifact 非长期证据库 |
| 2026-08-09 16:14 | 事实/UX | 修舆情结束日异步同步；移除首页无证据数字和过度营销 | Node 16 passed；聚焦 ESLint/diff-check 通过 | 需全量 Vue 回归和真实浏览器 |
| 2026-08-09 16:17 | 跨会话 | 创建本总控文档，固化范围、版本、工作池、自动发现和转接协议 | 文档自查待下一会话复核 | 搜索 WIP 与 prompt WIP 尚未收口 |
| 2026-08-09 16:32 | WIP 基线 + 数据质量 | 完整读取安全/审计/交接材料；增量前全后端与 Vue 基线通过；以 3 个失败用例驱动 quality profile v2 的来源/语言/月切片、schema drift 与 baseline/current 数量/cutoff 观测 | 后端 1459/1459（3 warning）；Vue 177/177；数据质量专项 17/17；`git diff --check` 通过 | 全量基线早于本轮并行改动最终合流，收口前必须重跑；切片仍是机械观测，无近重复/gold/批准阈值 |
| 2026-08-09 16:41 | 数据质量 + 事实/UX | profile v2 增加 artifact 完整性校验和安全比较 CLI；复算 20/358/6376 三个明确样本；修复舆情 null→0、缺口伪异常和未知分数进入排序 | 数据质量 23/23；舆情 Node 14/14；聚焦 ESLint、`git diff --check` 通过 | 样本不可外推；near-duplicate/事实/gold/阈值未测；图表 gap 仍需真实浏览器 |
| 2026-08-09 16:45 | 搜索/实体语义 | 修复 5 条 actor/target 猜测路径、语言缺失回退和层级筛选；新增 bounded search-eval-v1，并追加 unreviewed/k coercion/1000-query fail-closed | 搜索/V11/architecture/registry 133 passed（3 warning）；Node 21 passed；Ruff/ESLint 通过；Vue build 4270 modules 通过 | 只有 synthetic fixture 指标算法证据；无真实 qrels、200 专家问题、母语审阅、候选负载 |
| 2026-08-09 16:58 | 身份/隐私 + 持续审计 | 新写入拒绝不安全 provider URL、重复 key 和非有限 JSON；旧值在资料/导出/AKM 输出侧 fail closed。完成 130 项 registry/只读 runner 独立审查，并补未来证据时间、cutoff 和继承 PATH 绕过防护 | 身份/隐私/architecture 57 passed（3 warning）；continuous audit 12 passed；实时外部报告 130/130、206 locator、0 stale，重复写入 rc=2；`git diff --check` 通过 | provider 请求侧组合仍待助手任务合流；registry cutoff 早于后续修改；无 scheduler、retention、issue integration、候选或生产证据 |
| 2026-08-09 17:15 | 产品声明 + UX 空值 + 模型治理 | 新增受界公开声明扫描器，扩审关闭 markup/跨分句/策略文件 fail-open；修复 Ground News 缺值伪装为 0；模型治理区分 gold/silver/holdout 并增加 review 有效期与 baseline 元数据兼容性 | claim Python 12/12，仓库扫描 0 findings；Ground Node 11/11 + ESLint；governance Python 41/41（1 warning）、Node 10/10 + ESLint | claim automation 仍 `not_configured`；无真实浏览器/法务/gold/holdout/review artifact；baseline 审阅链时效仍在独立二审 |
| 2026-08-09 17:24 | Prompt/互动引用 + 官方连接器 | 独立核验 prompt registry、本轮 `GM-T` 引用和完整缓冲后 fail-closed，收口 provider 旧 URL 请求侧；连接器拒绝非有限/过深 JSON 和 control/backslash URL | prompt/config Python 54/54（2 warning），architecture/registry 33/33，Vue 22/22；connector Python 17/17（1 warning）；`git diff --check` 通过 | 引用不验真实性/事实/蕴含；无真模型/外站/许可/候选，不宣称 live |
| 2026-08-09 17:34 | 金融缓存 + 身份遗留值 + 可访问性 | 共享缓存改为有界 descriptor/no-follow/no-replace 写入并限制 TTL；旧 provider key JSON 歧义整体拒绝；收录页收口旧响应覆盖、空状态和键盘/移动源码边界 | financial trust 22/22；prompt/config 59/59（2 warning）；collections Node 5/5 + ESLint/SFC；`git diff --check` 通过 | 无实际缓存/服务/provider/浏览器/设备；仍需全量 Vue 在 model WIP 合流后重跑 |
| 2026-08-09 17:46 | 模型保障 + 助手工作区 | 模型祖先 review 过期动态传播、cohort/calibration 兼容、账本高水位与 review ID 防重放收口；助手默认目录拒绝链接越界、共享根用户名与临时文件覆盖，并将新 JSON 固定为私有 no-replace 写入 | model 独立 Python 79/79（1 warning）+ Node 8/8；根交叉 Python 55/55（1 warning）+ Node 12/12；workspace Python 13/13 | 模型仍为 manifest-only；工作区只验临时目录，未触碰真实账号/服务；可靠性和研究工作流并行 WIP 尚待合流 |
| 2026-08-09 17:56 | 可靠性 + 工作区租户边界 | monitor/heartbeat/history GET 零写，损坏账本/回退时钟/freshness 矛盾 fail closed，null/gap 不再伪装健康；助手锁与列表拒绝跨租户/外部链接，marker 精确版本及私有 JSON 写入补齐 | reliability 独立 Python 110/110（3 warning）+ Node 24/24 + build；根交叉 Python 162/162（3 warning）+ Node 24/24；workspace 交叉 55/55（2 warning）；`git diff --check` 通过 | 无 SLO/collector/真实流量/候选/浏览器；工作区未触碰真实账号，研究与证据账本仍在并行复核 |
| 2026-08-09 17:59 | 研究可复现性 | 项目整态/change/audit 三层摘要与单调 UTC 收口；严格写入 JSON、锁、URL/隐私边界；四格式 reviewed-draft v2 下载契约 | 根代理 Python 73/73（3 warning）；Node 13/13；独立任务 architecture/auth/candidate 147/147 与隔离 build 通过 | 不是签名/WORM；旧 v1 无迁移；无真实研究员/法务/来源/浏览器验收；证据账本与 IA 仍在并行复核 |
| 2026-08-09 18:09 | 助手文件与知识库边界 | 工作区/知识库列表、预览、下载、ZIP 统一拒绝 symlink/hardlink 与读中变化；预览 1 MiB、ZIP 500 项/无重复，错误不回显底层异常 | workspace 专项 22/22（1 warning）；workspace/defaults/privacy/architecture/browser 109/109（3 warning）；`git diff --check` 通过 | 仅临时目录/测试客户端；未触碰真实账号/文件/服务，未做恶意并发 race、负载或浏览器下载验收 |
| 2026-08-09 18:28 | 证据链 + 助手目录诚实性 | 证据 snapshot/event/review 绑定与前序链、严格 JSON/链接/时间/URL 边界收口；StoryGraph 维持 unknown 并区分布局线/影响假设。站点/成员 GET 不再生成演示数据，目录字段不冒充实时观测 | evidence 独立 Python 77/77 + Node 22/22；根交叉 50/50 + 17/17；file-store/workspace 45/45；Assistant Node 23/23；聚焦 ESLint 通过 | 无真实来源/账本/DB/目录/浏览器/负载；哈希非签名/WORM；IA/实体并发 WIP 尚待终验 |
| 2026-08-09 18:38 | 实体治理 + 候选合同 | 严格 ledger/route/catalog、稳定 ID/BCP47/alias 冲突/审批证据与前端身份裁决收口；新增必选 `review_expiry_policy=not_configured` 诚实状态，读模型升 v2，候选验证器拒绝缺失/夸大状态 | 独立 Python 224/224 + Node 9/9 + builds；根交叉 213/213 + Node 9/9；增量 candidate/browser 148/148；ESLint/diff-check 通过 | 21 种子全部 review_required、accuracy not_measured；只披露未配置，未强制到期；无母语/gold/真候选/浏览器 |
| 2026-08-09 18:55–19:00 | 信息架构 + 国家档案 + 翻译 provenance | IA 静态入口/深链/认证回跳/preload/CSP/索引契约收口；国家档案增加固定 blocked 的 schema-only catalog/page；翻译端点增加认证、loopback、严格请求/响应与源文哈希 receipt，前端增加工作量边界、迟到隔离及诚实披露 | IA Node 204/204；country/translation/IA Node 28/28；合流 backend 176/176（3 warning）；focused ESLint、feature registry 18 features/0 violations、`git diff --check` 与隔离 build 4285 modules 通过 | 无国家事实/owner/母语验收；无真实翻译模型、准确率/术语/人工审阅；未跑候选/浏览器/服务 |
| 2026-08-09 19:10–19:15 | 收藏 + 定时简报 + API 契约 + 审计矩阵 | 收藏批量 set/冲突与严格 JSON 纵切片、调度零写/租户/文件与时钟边界收口；新增 admin-only OpenAPI 摘要及源码限流说明并消除重复 operation ID；依据证据更新五项为 PARTIAL | 收藏交叉 Python 111/111 + Node 49/49；schedule Python 147/147 + Node 28/28；根合流 Python 150/150；continuous audit 12/12、130/130、222 locator/115 evidence/0 stale、重复写 rc=2；feature registry release-ready 18 features/31 entries/0 violations；import boundary PASS | 无真实 DB 竞争/完整集合 UI；无真实 scheduler/通知/分布式语义；API 版本/限流/支持未批准或运行观测；audit automation/retention 仍 not_configured |
| 2026-08-09 19:40–19:43 | 地理/搜索 + 反馈训练 + 金融来源 + API 契约 + 审计矩阵 | country 不再冒充 language/location，语言与未核验地理 metadata 分列；地理四角色 schema 纵切片；反馈默认禁止训练且严格最小化；来源贡献只作可用性门禁；OpenAPI 敌手边界与失败缓存头收口；更新三项为 PARTIAL | 搜索专项 Python 13/13、交叉 164/164 + Node 32/32；AI-12 Python 316/316 + Node 24/24；金融 Python 196/196 + Node 11/11/typecheck；WF-14 根组合 93/93；continuous audit 12/12、130/130、231 locator/121 evidence/0 stale、重复写 rc=2 | 无四角色回填/索引/UI、训练 owner/lineage/保留、人评/许可/权重、真实限流/版本策略；audit automation/retention 仍 `not_configured` |
| 2026-08-09 20:00–20:02 | 状态/事件 + 全局主张覆盖 + 引用导出 + 金融方法 + 审计矩阵 | 维护历史严格只读并区分未配置/不可用/验证为空；离线公开处置不猜负责人/ETA/SLA；7 个衍生产出覆盖门禁如实报 11 缺口；四格式稳定脚注/安全 locator；金融现状算法绑定非均匀向量；同步六项 PARTIAL 证据 | 根相关 Python 73/73、金融 73/73、continuous audit 12/12；相关 Node 36/36；feature registry/import boundary PASS；130/130、238 locator/128 evidence/0 stale、重复写 rc=2 | 无正式 incident 流程/SLO/订阅，claim 覆盖仍 partial，无 source truth/entailment/正式引用样式/永久链接/批准指标方法；automation/retention 仍 `not_configured` |
| 2026-08-09 20:25–20:30 | 数据质量 + 搜索时间 + freshness UX + 主张契约 | 质量剖析升 v3 并加入有界近重复候选、三样本复算与严格 JSON/硬资源上限；SR-08 分列四类时间并关闭 legacy/canonical 矛盾；全局提示 generation key 去除本地接收时间；研究/故事图/金融/舆情逐主张契约把覆盖清单降至仅两个助手 claim-ID 缺口；根扩审又关闭金融畸形 series ID 抛异常 | quality/architecture 144/144；搜索 Python 61/61 + Node 23/23；operations 13/13、相关 Node 26/26；claim checker `partial`、2 findings；financial Node 14/14 + typecheck | 近重复候选未经人审且有预算溢出；时间字段无真实迁移/回填；无真浏览器；claim ID/unknown 结构不证明 source truth/entailment，助手正文仍非结构化且两缺口必须保留 |
| 2026-08-09 20:41–20:49 | 舆情/金融二审 + 持续审计 | 关闭舆情 top-event 未覆盖计数和 target 路径错绑；FR-07 只披露有界序列点且客户端复算实际点数，不冒充独立样本/CI；刷新六项登记证据并生成 no-replace 外部报告 | 舆情根 Python 17/17 + Node 17/17；FR-07 Python 101/101 + Node 15/15/typecheck/build；continuous audit 12/12；130/130、247 locator/137 evidence/0 stale、重复写 rc=2 | 舆情与趋势均无真实来源/方法/人工验收；两个 assistant claim-ID finding 保留；审计 automation/retention 仍 `not_configured` |
| 2026-08-09 21:18–21:30 | 搜索行为证据 + 数据质量 + 来源方法 + 持续审计 | 6 预设及 direct/V11/legacy actor 毒值矩阵让 SR-05/06 源码级升为 PROVEN_CODE；FR-09 catalog 投影保持 quality unknown/release blocked；EV-05 隐藏 raw note、安全化 locator 且不把目录标签当事实性；continuous audit v2 明示 HEAD 不绑定 dirty WIP | 搜索 Node 24/24 + Python 116/116；FR-09 30/30、组合 83/83；EV-05 根 backend 13/13 + Node 17/17、claim scanner 0；continuous audit 14/14；130/130、253 locator/143 evidence/0 stale、重复写 rc=2 | 不证明搜索命中/真 DB、全库质量、目录评级真值或候选浏览器；automation/retention 仍 `not_configured`，dirty worktree 无内容 hash |
| 2026-08-09 21:49–22:00 | 搜索语义 + 产品地图 + 首页状态 + 图谱抽样 + 舆情三轴 + 持续审计 | exact/fuzzy 统一成“全部词/主题扩展”版本契约且明确非向量；首页模块状态只称 contract_validated；公开帮助新增 8 模块/9 条显式任务交接；图谱核心抽样 provenance 与舆情 response_projection 三轴证据同步，五项均保持 PARTIAL | SR-04 Node 49/49、Python 88/88、feature/import/ESLint/build 4293；IA 聚焦 11/11；AI-05 Python 80/80 + Node 24/24；claim scanner 0；continuous audit 14/14；130/130、261 locator/151 evidence/0 stale、重复写 rc=2 | 无多语言 qrels/真实 lineage/数据真值/图全集/上游轴独立性/候选浏览器；automation/retention 仍 `not_configured`，dirty worktree 未计算内容 hash |
| 2026-08-09 22:10–22:24 | 搜索高亮 + 关系本体 + 模型面清单 + 研究导出 + 持续审计 | 正向原样词项以受界 offset/文本 mark 显示且不冒充相关性；并行/时间关系不可升级为影响/因果；六个模型输出面显式 unknown；四格式导出加入字段 allowlist、敏感排除与许可 unknown；五项均保持 PARTIAL | 搜索 Python 289/289 + Node 29/29；关系 Python 151/151 + Node 42/42；AI-03 Python 100/100 + Node 12/12；WF-11 Python 167/167 + Node 14/14；当前全 Vue feature 279/279、build 4298；audit 14/14、130/130、266 locator/156 evidence/0 stale、重复写 rc=2 | 无真实 DB/多语言召回/历史图回放/runtime model attestation/许可与正式签发/候选浏览器；audit automation/retention 仍 `not_configured`，dirty worktree 无内容 hash |
| 2026-08-09 22:45–23:00 | 身份安全 + 保存检索监测 + 图指标 + 国家制度 schema + 持续审计 | 身份账本/JSON/前端能力状态 fail closed；保存检索显式披露 monitor 未配置；16 项图指标统一 unknown/layout-only 方法卡；CD-02 增加 27 字段空事实制度 schema；四项均保持 PARTIAL | QA Python 240/240 + Node 14/14；SR-10 Python 52/52 + Node 17/17；EG-11 Python 143/143 + 前端 74/74；CD authoritative 170/170；architecture/registry/candidate 125/125；audit 刷新见本轮外置报告 | 无企业 IdP/设备信任、真实 scheduler/delta/通知、图指标方法/evidence/全旁路、国家事实/法源/许可/母语研究；audit automation/retention 仍 `not_configured` |
| 2026-08-10 03:51–04:07 | 截止后总收口 + AI-11 + 持续审计 | AI-11 五项反例收口并保持 manifest-only/unknown；达到 04:04:29 UTC 后刷新 130 项矩阵、全量回归、声明/架构/registry 与外置 no-replace 报告 | 全后端 2099/2099（7 条既有 warning）；Vue feature 291/291、全 ESLint、隔离 build 4301 modules；financial 15/15 + typecheck；截止后专项 62/62；claim checker 按设计 partial/2 finding；最终 audit `130/130` / 275 locators / 164 evidence / 0 stale，首次 rc=0、重复写 rc=2，外置产物 `/tmp/globemind-continuous-audit-20260810-0405-final.DyH4gH` | 无 release/service/真实 DB/provider/candidate/browser/human acceptance；automation/retention 未配置，dirty worktree 不作内容 attestation，Ruff 不在锁定 runtime |
| 2026-08-10 05:22–05:39 | 新轮接管 + 国家/qrels/AI/浏览器/审计 | 完整复读安全、总控、审计与版本进度；锁定运行时核验 WIP/130 登记；新增 CD-05 空事实法源 schema、真实 qrels 输入门禁、互动可选结构化 claims、browser smoke v2 双视口 fixture 配对及六项 manual-only audit plan；CD-05 升为 PARTIAL | 接管专项 159/159；最终跨域组合 300/300（3 warning）；国家前端 5/5；公开声明 0 finding；claim coverage 按设计 partial/2；audit 15/15，机器快照 130/130、284 locator、172 evidence、0 stale，plan 6 validators/6 domains/not_executed，首次 rc=0/重复写 rc=2，外置 `/tmp/globemind-continuous-audit-20260810-0533.Za0O4B` | 无国家事实/真实 qrels/默认全结构化输出/真候选浏览器；validator plan 未执行，scheduler/retention/owner 仍 `not_configured`；脏工作树无内容 attestation |
| 2026-08-10 05:40–06:01 | AI 结构化引用 + 浏览器语义探针 + 手工审计编排 | 四个互动出口与定时报表保存路径强制逐 claim JSON/服务端 ID/来源或 unknown；claim inventory 静态缺口降至 0；国家不可用页加入双视口规范化文本哈希；六域 runner 以最小环境和无正文证据实际执行 | 全后端 2119/2119（7 条既有 warning），国家前端 5/5；claim coverage 7 surfaces/28 capabilities/0 finding；manual validators 6/6 passed，外置 `/tmp/globemind-validator-run-20260810.cVIO31`；final audit 130/130、285 locator、173 evidence、0 stale，首次 rc=0/重复写 rc=2，外置 `/tmp/globemind-continuous-audit-20260810-0552.tph25m`；公开声明与 diff-check rc=0 | 不证明 claim 切分完整、source truth/entailment/事实；无真实 qrels/候选/浏览器/设备；scheduler/retention/issue/owner 仍 `not_configured`，dirty WIP 无内容 hash |
| 2026-08-10 06:02–06:30 | 国家法源 bundle + 真实 qrels benchmark 链 + 五页浏览器语义 + 定时只读审计 | CD-05 新增原文/条款/许可/时态/修订/双角色审阅的离线 bundle intake；SR-03 新增 corpus/guide/adjudication qrels bundle 与 frozen ranked-run benchmark receipt；browser fixture 时间固定并扩为五页九探针；GitHub workflow 增每日只读审计与 30 天指定产物留存，计划扩为八项，issue/人工闭环仍不配置 | 专项合流 114/114（2 条既有 warning）；全后端 2131/2131（7 条既有 warning）；国家 Node 5/5；公开声明 0 finding；claim coverage 7 surfaces/28 capabilities/0 finding；锁定 runtime validators 8/8，外置 `/tmp/globemind-validator-run-20260810-final.TNIFBg`；final audit 130/130、287 locator、175 evidence、0 stale、plan 8 validators/6 domains/configured_not_observed，首次 rc=0/重复写 rc=2，外置 `/tmp/globemind-continuous-audit-20260810-0632.PQSPY7`；diff-check rc=0 | 仓库无真实国家文档/qrels/corpus/ranked run；未运行候选浏览器、设备或 GitHub 定时任务；不证明 source truth/entailment/相关性提升；issue integration、具名 owner 与 dirty WIP 内容 attestation 仍缺 |
| 2026-08-10 06:31–06:54 | pilot readiness + qrels 多语切片 + AI 工件绑定 + browser a11y + 自动分诊 | 国家批准 plan 与法源 bundle exact-hash 对账且不发布；translated-intent 计划拒绝 cherry-pick 并输出未阈值化语言/国家/overlap 切片；claim ID 绑定 exact source artifact hash；五页九探针增加 role/live/atomic/label-presence；workflow 增 content-free triage 与第九验证项 | 跨域合流 156/156（2 条既有 warning），格式化后聚焦 99/99；全后端 2141/2141（7 条既有 warning）；相关 Node 55/55；Ruff/ESLint/公开声明/diff-check rc=0；锁定 runtime validators 9/9，`/tmp/globemind-validator-run-20260810-0647.rzxJoi`；audit 130/130、291 locator、179 evidence、0 stale，`/tmp/globemind-continuous-audit-20260810-0647.WCwcJL`；triage 正确仅报 dirty finding 且 `human_triage_required`，`/tmp/globemind-audit-triage-20260810-0647.coQ4YH`；audit/triage 重复写均 rc=2 | 无真实批准 pilot/法源/qrels/corpus/ranked run/source truth；无候选/设备/GitHub schedule 观测；parity/entailment/WCAG 未建立；trend baseline、issue、具名 owner 与 dirty WIP attestation 仍缺 |
| 2026-08-10 06:55–07:25 | 国家 claim + 同 qrels 回归 + 外部 AI/browser 证据 + 审计趋势 | 国家 statement SHA 绑定 exact document/anchor 且冲突/许可/时态 fail closed；同 qrels/plan/query/group baseline-current 只报原始 delta；AI 外部观察重算 source/inventory/claim ID；browser 外部报告重核 13 页×2 视口、5 个业务页/9 selector/18 probe 和 26 PNG；trend 比较两轮无内容 triage | 聚焦合流 104/104（2 warning）；claim 兼容探针修复后 21/21；全后端 2152/2152（7 条既有 warning）；Ruff、公开声明、claim coverage 7 entries/0 findings、diff-check 通过；validators 9/9；audit 130/130、296 locator、184 evidence、0 stale，`/tmp/globemind-continuous-audit-20260810-0714.eAbGx8`；triage `/tmp/globemind-audit-triage-20260810-0714.OKdkjl` 仅保留 dirty finding；trend `/tmp/globemind-audit-trend-20260810-0714.TcqzrD` 无新增/解决/validator 退化，三类输出重复写均 rc=2 | 无真实国家文档/claim plan/qrels/ranked runs/模型输出/候选浏览器；source truth、entailment、回归阈值、WCAG 与发布均未建立；自动历史 artifact 获取、issue、具名 owner、真实 CI 观测和 dirty WIP attestation 仍缺 |
| 2026-08-10 07:25–08:09 | 最终快速收尾 | 检查五类新增实现、导出与文档；修复 browser stub evidence 夸大候选验收、AI/browser release 路径与读中身份边界、browser/trend 非有限 JSON、qrels `1e400→inf`，以及 syntax test 显式写源码 `.pyc`；新增最终交接清单，不新增能力或空 schema | 聚焦 95/95（2 条既有 warning），bytecode/审计专项 30/30；最终全后端 2153/2153（7 条既有 warning）；validators 9/9，`/tmp/globemind-validator-run-20260810-closeout.RHVZpF`；audit 130/130、296 locator、184 evidence、0 stale，`/tmp/globemind-continuous-audit-20260810-closeout.oBUnMe`；triage `/tmp/globemind-audit-triage-20260810-closeout.jxkE2z` 只保留 dirty finding；trend `/tmp/globemind-audit-trend-20260810-closeout.4Ld2Tg` 无新增/解决/validator 退化，五类状态 delta 全为 0 | 无真实国家资料/qrels/ranked runs/AI replay/候选浏览器；Ruff 不在锁定 runtime；既有源码缓存未删除；自动历史 artifact 获取、issue、具名 owner、事实/蕴含/阈值/WCAG/发布仍未建立 |

后续日志模板：

```text
| YYYY-MM-DD HH:MM | 域 | 发现、修改文件与真实结果 | 精确命令/测试数量 | 未验证范围与下一项 |
```

## 11. 最终收口报告必须回答

1. 130 项中分别有多少 `PROVEN_CODE / OBSERVED_SAMPLE / PARTIAL / EXTERNAL_BLOCKED / NOT_STARTED_OR_UNVERIFIED`？
2. 12 小时内跨了哪些域，为什么没有死磕单点？
3. 搜索质量到底提升了什么，哪些有 qrels 指标，哪些仍只是功能契约？
4. 数据质量在哪些明确样本上测出什么，为什么不能外推？
5. Prompt、引用、实体、来源、研究工作流、安全、隐私、可访问性各完成到哪里？
6. 全量测试和构建的最终数字是什么？失败是否全部解释？
7. 当前 V0.9/V1.0/V1.5/V2/V3 各自发布状态是什么？
8. 哪些事 AI 已经做完，哪些必须申请人、数据、许可、服务器、候选和预算？
9. 自动审计 runner、调度和 artifact 保留是否真的配置？若没有，必须写 `not_configured`。
10. 是否达到最早收口时间；若未达到或矩阵不完整，继续保持目标 active。

## 12. 一句话转接指令

见用户回复；其内容应要求新会话读取本文件、遵守 `AGENTS.md`、核验当前 WIP、续跑至最早收口时间并从最高优先级跨域任务继续。
