# V0.9“可信止血版”实施进度

更新时间：2026-08-09（Asia/Shanghai）  
依据：`/root/globemind-audit-round2-2026-08-09/implementation-handoff.md` 与同目录完整 10 轮审计报告  
状态：源码修复与相关离线验证已完成；未授权、未执行生产发布；仍有候选环境和外部责任门禁未通过

## 安全与范围边界

- 仅在 `/root/data/globemind` 源码仓库工作，保留已有未提交改动。
- 未导入、修改或清理 `current`、`previous`、版本化或 rejected release。
- 未停止、重启、接管或迁移任何运行服务和长管线。
- 线上复测仅使用公开、只读 HTTP 请求；输出经过字段收敛，不记录凭据、进程参数或内部连接信息。
- Python 测试统一使用角色锁定运行时 `/root/data/python-runtimes/globemind-web/1.0.0/bin/python`，并在启动前设置 `PYTHONDONTWRITEBYTECODE=1`、使用 `-B`。

## 修复前基线

2026-08-09 对 `https://dev.globemind.top` 的公开只读复测：

| 检查 | 结果 | V0.9 判断 |
|---|---|---|
| `/api/financial/dashboard` | `mode=live`、`cache=stale`；Ground News 记录为 0；仍返回 8 个精确复合指数及涨跌幅 | 不通过：必须进入不可计算/历史状态 |
| 数据源状态 | 38 个源中 31 live、4 degraded、3 disabled | 必须公开覆盖率，并按关键输入而非简单多数表决门禁 |
| `/showcase` | HTTP 200 | 不通过：生产实验/NFT 路由需移除 |
| `/privacy`、`/terms` | HTTP 404 | 不通过：稳定治理入口缺失 |
| `/api/ops/heartbeat`（GET） | HTTP 404 | 基线请求方法错误；代码契约实际为 `POST`，后续已通过前后端契约测试核实 |
| Vue feature tests | 87/87 通过 | 作为改动前回归基线 |

说明：SPA 路由返回 HTTP 200 不等于页面语义正确；帮助页、404 和迁移页还需浏览器级验证。

## V0.9 工作清单

状态定义：`TODO` 未开始；`WIP` 实施中；`DONE` 已通过聚焦测试；`BLOCKED` 依赖外部决定或发布窗口。

| 工作包 | 审计映射 | 状态 | 验收条件 | 代码负责人 |
|---|---|---|---|---|
| 复合指数可信门禁 | FR-04/05/06/07 | DONE | stale、关键源缺失或覆盖不足时不返回/展示任何复合精确值和告警；缺失/损坏 trust 也必须失败关闭 | financial_trust_gate + trust_gate_review |
| Ground News 历史模式 | FR-01/02/03、QA-12 | DONE | 状态驱动文案；stale/missing 下无“实时/今日/本周/LIVE”误导；提示不遮挡操作 | historical_story_state |
| Story Graph 空结果原子清理 | EG-01/02 | DONE | 新查询 0 结果、失败或超时时清空旧图、统计和结论；晚到请求不能回填 | historical_story_state |
| 实验路由与帮助入口 | IA-01/02/03 | DONE | showcase/debug 仅 DEV；帮助页可发现；`/data-statistics` 明确迁移 | routes_governance_a11y |
| 公开治理网页入口 | QA-06/07/15 | DONE（限网页入口） | 隐私、条款、安全、纠错、方法和来源入口稳定可发现；不伪造负责人或 SLA | routes_governance_a11y |
| sitemap/canonical/security.txt | QA-11/15 | DONE（工程入口）/ 外部责任 BLOCKED | 以仓库既有公开 origin `https://globemind.top` 生成 public sitemap、按路由 canonical/robots；仅精确开放 `/.well-known/security.txt`，共享邮箱不冒充具名负责人或 SLA | primary + 待安全负责人 |
| 认证表单无障碍 | QA-04/05/06/12 | DONE（限认证页） | Login/Register/Forgot/Reset 具有程序化标签、焦点、状态消息和知情同意 | routes_governance_a11y + primary |
| 注册数据最小化与保留期 | QA-06/14 | BLOCKED（需产品/法务决策） | 姓名、手机号等字段是否必需及具体保留/删除期限需正式审批；当前页面不得声称已完成该审核 | 待产品/法务/隐私负责人 |
| 搜索表单无障碍 | QA-04/05 | DONE | 唯一 H1；关键词、时间、来源、语言、分页和文件夹控件具有程序化标签 | routes_governance_a11y |
| Ground News 旧深链 | IA-07 | DONE | search/blindspot 旧路径明确说明迁移去向并由用户手动选择，不静默跳首页 | routes_governance_a11y |
| 普通主题检索语义 | SR-01 | DONE | 未加引号的多词主题匹配全部词，不再被当作一个完整短语；只有显式引号进入短语匹配 | primary |
| 导出、收藏和助手登录恢复 | WF-01/02/03 | DONE | 导出成功/失败有明确反馈；访客收藏明确仅本机；助手登录前保存并恢复上下文；同步失败不吞掉 | primary |
| heartbeat 契约 | QA-09 | DONE（无代码修改） | 已确认客户端与后端均使用 `POST /api/ops/heartbeat`，无需把正确代码改成 GET | historical_story_state |
| 舆情/AI 结果可信状态 | AI-01/02/03/04/06 | DONE（V0.9 隐藏策略） | 截止日期及覆盖必须由当前查询实际贡献样本得出；不可信或缺评测证据时统一隐藏派生精确分数 | primary + historical_story_state |
| 关键页面超时/黑屏降级 | IA-04 | DONE | Amazing Globe 10 秒加载握手、明确错误/静态范围降级、重试与可访问性状态 | primary + routes_governance_a11y |
| 桌面/移动数据一致性 | QA-01 | BLOCKED（候选环境浏览器门禁） | 同一时刻、同一响应快照下桌面/移动显示一致；加载中不以 0 假充结果 | 待独立候选环境浏览器验收 |
| 移动触控与遮挡清单 | QA-03/12 | WIP | 关键控件达到 44×44px，提示条不遮挡内容，图谱提供可用的列表替代 | primary + 待独立可用性验收 |
| 关键结果可信元数据清单 | V0.9 总门禁 | WIP | 按页面盘点 cutoff/coverage/source/model 字段；未接入门禁的派生精确分数不能默认为已验收 | primary + 待数据/模型负责人 |
| 数据/模型/服务责任人 | V0.9 治理项 | BLOCKED（需组织任命） | 工程已公布职责矩阵并诚实标“待指定”；但 V0.9 要求的真实负责人仍需组织正式任命 | 待产品/数据/模型/服务组织负责人指定 |

## 已完成改动

### 关键操作反馈与登录恢复

- CSV 导出在未选数据、生成成功和浏览器失败三种状态下都有可见及 `aria-live` 反馈。
- 访客收藏明确标识为浏览器临时数据；登录用户的远端同步成功/失败均有反馈。
- “数据助手研判”在登录前冻结当前检索和勾选素材；登录成功后恢复上下文并在原页面打开助手。
- 登录弹窗补齐 dialog 语义、程序化标签、自动填充属性和断言式错误消息。

搜索契约聚焦验证：Node 15/15；Python 搜索应用/性能契约 11/11。

### 历史模式与空结果状态

- 全站新鲜度提示由 `/api/health/features` 状态驱动；未完成核验、缺失、过期、离线和请求失败均 fail closed 为历史资料模式。
- Ground News 以同一状态移除 LIVE/今日/本周等误导，展示截止时间、lag、SLA 和近 7 日可用量。
- Story Graph 的列表、工作区和证据请求均有 AbortSignal 与 generation gate；加载、0 匹配、0 节点和失败均先原子清空旧图、统计、焦点和助手上下文。

聚焦验证：Node 29/29；完整 frontend feature tests 当时快照 99/99；聚焦 ESLint 通过。

### 生产路由、治理与认证

- `/showcase`、`/showcase/delta-force` 与 debug route 只在 `import.meta.env.DEV` 注入；生产 SPA fallback 同步移除 showcase。
- 新增无需登录的帮助、隐私、条款、安全、纠错、方法和来源页；帮助页、AboutUs、全局导航与注册页均可发现。
- 治理页对未完成的许可、保留期、处理者清单和负责人保持明示“待指定/未完成”；通用邮箱只宣称受理/转交，不虚构 SLA。
- `/data-statistics` 改为迁移说明和手动选择，不再静默改变路由语义。
- `/data-service/ground-news-search` 与 `/data-service/ground-news-blindspot` 改为明确的迁移说明页，不自动恢复旧条件、不静默跳首页。
- Register/Login/Forgot/Reset 补齐 label-for/id、autocomplete、aria-live 和错误/成功焦点；注册增加必选知情确认，登录 redirect 只允许站内安全路径。
- 搜索页增加唯一 H1，主/包含/排除词、起止时间、数据源、语言、文件夹和分页控件均完成 label/id/name 关联。
- 新增 `robots.txt` 与仅包含稳定公开页面的 `sitemap.xml`；SPA 服务端以 `Link rel=canonical` 和 `X-Robots-Tag` 区分公开与认证页面，客户端同步更新 canonical。登录、用户中心、研究工作台、模型保障、实验页和未知页默认 `noindex`。
- `/.well-known/security.txt` 作为唯一精确审查的 dot-path 例外由 API 生成；其它 dotfile 仍 404。文档只登记共享受理邮箱、公开政策、语言和到期时间，不宣称具名 owner 或响应 SLA。

早期独立验证快照：治理 5/5、导航 4/4、静态路径 43/43，聚焦 ESLint 通过。最终合并验证见下方“已完成工程验证”。

### 关键页面降级

- Amazing Globe 不再以默认 canvas 尺寸当作就绪信号；子页加载状态完成真实转移后才通过受限 `postMessage` 握手确认 WebGL。
- 父页同时校验 `origin` 和 iframe `source`；10 秒内未就绪则进入可重试的错误/静态范围降级，不再无限黑屏。

### 金融复合指数与告警门禁

- 根级/嵌套 trust 缺失、schema 损坏、snapshot 不一致或 computability/coverage/reason 矛盾时一律 fail closed。
- 只有节奏内、时间戳有效且存在有效观测的来源计入覆盖；`live + 0 records` 不计入，配置不能突破 50% 安全下限。
- 按时事、宏观、安全、能源、物流、科技和社会输入组明确受影响指数；任一必需组不足时隐藏相关复合输出。
- 门禁失败清空指数 value/change/spark、所有 `IDX-*` points/latest/change、K 线/均线与告警；API 或轮询失败时 React 第二道 sanitizer 同样清除旧精确值。
- 告警在规则计算和历史刷新前先过门禁；读端点返回结构化 `paused/trust/reason/coverage/schema/snapshot` 契约。

主流程复验：金融后端 63/63（子任务更广组合 73/73）；React trust 3/3；TypeScript typecheck 通过；Vite 生产构建 51 modules 通过。

### 舆情派生分数门禁

- cutoff/coverage 只统计当前查询过滤后、数值有限、方法版本正确、未被拒绝且实际对终点观测有正权重贡献的样本；无有效 cutoff 时保持 `null`。
- trust 缺失、schema 损坏、版本/快照/来源不完整，或 `is_computable` 与 status/trust_status/computability/display_mode 冲突时一律 fail closed。
- 统一 sanitizer 覆盖 trend、overview、dimensions、families、top_event、分布、impact、confidence、relevance 和 quality 等精确派生值；保留文章、计数与来源证据。
- 后端每次缓存读取重新验鲜并 sanitize；前端缓存读取、刷新失败和页面长时驻留到期时均原子清除旧复合值。
- schema/model/method/source/snapshot 元数据完整展示，不截断版本或快照 ID；空值不再显示成 0、正向、常规或 0% 置信度。
- 当前仓库没有可信的 ground-truth、F1、校准或漂移评测产物；V0.9 选择隐藏对应精确质量值，不生成伪评测。

主流程复验：舆情后端 74/74；Node 聚焦 13/13；聚焦 ESLint 通过。

## 已完成工程验证

- 同一最终工作树上的相关 Python 合并套件：223/223；使用锁定 Web 运行时、`PYTHONDONTWRITEBYTECODE=1` 与 `-B`。
- 全部 Vue feature tests：109/109；全量 Vue ESLint 通过；`git diff --check` 通过。
- Vue `build:main-only`：4,248 modules，3 分 21 秒，exit 0；仅既有 chunk-size warning。
- 最终 Vue 产物不含 `/showcase` 路径或 Showcase/Delta chunk；含 3D 健康握手脚本、公开治理 chunk 和旧 Ground News 迁移 chunk。
- 金融 React trust tests：3/3；TypeScript typecheck 通过；Vite 生产构建 51 modules，exit 0。

## 仍待发布前门禁

- 运行发布 runbook 规定的完整 repository 离线质量门禁；本轮 223 项是受影响后端套件，不代表整仓所有测试。
- 对构建产物执行真实浏览器回归：桌面与移动分别验证 stale/empty/error/timeout、触控目标和关键操作。
- 任命数据/模型/服务负责人，完成法务/隐私/许可审批，并确认注册字段最小化与保留期。
- 盘点并升级仓外告警客户端；在独立候选环境复测新响应契约。
- 任何生产发布、服务重启或管线维护均需另行授权并按 runbook 执行。

## 未决风险

- 当前工作树在本轮开始前已有大量用户改动；完成记录必须区分本轮改动和既有改动，不能用 reset 清理。
- 法律、隐私、许可证和责任人最终文本需要产品、法务和安全负责人审批；工程可先提供真实的占位状态和联系入口，不能宣称已完成外部审核。
- 数据阈值必须基于关键输入清单和可解释权重；不能仅用“在线源过半”替代研究可用性判断。
- `GET /api/financial/alert/rules` 由裸数组升级为带 `paused/trust/reason` 的结构化对象；仓内 React 已同步，独立候选环境验证前需盘点并升级仓外客户端。
- sitemap/canonical/security.txt 的工程入口已按仓库既有公开 origin 实现；若组织未来变更主域，必须同步更新前后端 canonical、sitemap、security.txt 并重新验证。具名安全负责人、响应 SLA 与独立披露流程仍未获组织批准。
- 尚未完成真实桌面/移动浏览器的 stale、empty、error、timeout 组合回归；单元、契约和生产构建通过不等于该门禁已通过。
- 本记录不等于发布批准或线上修复完成证明。
