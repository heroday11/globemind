# V0.10 候选 HTTP 验收

`deploy/candidate_smoke.py` 是 V0.10 候选发布的黑盒 HTTP 门禁。它只访问已经启动的
候选端口，不导入后端源码、不连接数据库、不读取环境文件，也不启动、停止或向任何进程
发送信号。

## 前置条件

1. 候选必须由固定 Web runtime 和 schema-v3 release 在独立端口启动。
2. `--expected-build-id` 必须与该 release 的 `release.json.build_id` 完全一致。
3. `--base-url` 必须是候选 origin，例如 `http://127.0.0.1:18091`，不能包含路径、
   query、fragment 或凭据。
4. `--output-dir` 必须不存在或为空。工具拒绝覆盖已有证据。
5. 由受控本机过程签发短期候选 Bearer token，写入绝对路径、调用者所有、单硬链接、
   非符号链接且精确 mode `0600` 的普通文件。文件内容必须为 32 到 16384 字节的单行
   UTF-8 token；不得把 token 值放入 argv、环境变量、日志或 evidence。验收后删除该文件。

## 执行

```bash
BUILD_ID=0.10.0-YYYYMMDDTHHMMSSZ
EVIDENCE=/root/data/runtime/globemind/web/candidate-${BUILD_ID}

PYTHONDONTWRITEBYTECODE=1 \
/root/data/python-runtimes/globemind-web/1.0.0/bin/python -B \
  deploy/candidate_smoke.py \
  --base-url http://127.0.0.1:18091 \
  --expected-build-id "$BUILD_ID" \
  --auth-token-file /run/globemind-secrets/candidate-${BUILD_ID}.token \
  --output-dir "$EVIDENCE"
```

退出码：

- `0`：所有 required check 通过。
- `1`：候选已完成验收，但至少一个 required check 失败或被阻断。
- `2`：参数、输出目录或工具执行本身有错误，不能形成有效验收结论。

## 覆盖范围

门禁当前包含 49 项 required 检查：

- liveness、readiness、数据库 readiness、详细 feature health 和两处 release build identity。
- 根 SPA HTML、`#app`、GlobeMind 标题及实际 module entry JavaScript。
- 缺少凭据、无效 Bearer token、错误登录三种稳定 `401` 语义。
- 复用短期候选 Bearer 会话鉴权读取 `/api/ops/runtime-catalog`；要求只读控制面、固定
  12 项 V1 service ID、全部 `catalog_status=current`、0 drift、无授权控制动作，并递归
  拒绝 `secret_refs`、`secret_policy`、credential/password/token 字段及凭据路径。
- 复用同一短期身份只读核验 research storage、model assurance、MFA/session assurance、
  service-level measurement 和 entity-governance；不创建研究项目、评测、身份设置、SLO
  目标或实体治理 mutation。
- 只读核验账号删除影响计划固定为 `deletion_performed=false/execution_state=blocked`、
  处置分类和汇总一致、外部阻塞项完整且没有正文、路径或其他主体身份；不登记删除申请，
  更不会执行删除或匿名化。
- dashboard stats、search options、financial dashboard、隐私最小化 financial alert triage、
  Ground News list、opinion quality 和正式 data-governance catalog。
- `/api/graph` 的 universe、macro、briefing、micros、tree、search、micro、news、news-batch
  九个公开接口。
- V11 current hierarchy 的搜索及 `L3 -> L2 -> L1 -> news` 三段展开。
- 抽样 reader 正文与 paragraph anchor/excerpt/body hash 的主张级证据交叉核验。
- 九个 retired opinion endpoint 的稳定 `410 endpoint_retired` 契约。

Graph 和 V11 的标识只从公开 HTTP 响应获取。工具不会直接查询数据库，也不会从候选
release 或工作区导入 Python 模块。

Service-level 只验证观测合同与聚合自洽；目标仍必须是 `not_approved`、合规必须是
`not_computable`。Entity Governance 可以在零 approved、全部 `review_required` 时通过
“能力存在”检查，但这不代表实体质量已验收。Financial triage 只读取公共聚合并拒绝
actor/reason/audit 泄漏；它不会执行确认、升级、误报、解决或复盘写入。

## 空图谱策略

空图谱不是可接受的生产降级。如果 universe 没有可用于验收的关联 L3/L2 样本：

- universe 响应本身仍按实际契约记录；
- `graph_sample_availability` 明确失败；
- 依赖标识的 graph 和 V11 项标记为 `blocked`；
- `skipped` 保持为 `0`，总体状态为 `failed`，进程退出码为 `1`。

因此不存在通过 `skip` 掩盖候选数据或权限错误的路径。

## 证据格式

输出目录包含：

```text
acceptance.json
checks/001-health-live.json
checks/002-health-ready.json
...
```

`acceptance.json` 是完整 machine-readable 结论，每个 `checks/*.json` 是对应检查的独立
脱敏摘要。证据只保存状态码、耗时、content type、响应字节数、SHA-256、受控计数和契约
判断，不保存响应 body、请求 body、Authorization header、token、token 文件路径、password
或 secret。
响应有固定大小上限，HTTP redirect 被拒绝，代理环境变量不会被使用。

发布记录至少应保留 `acceptance.json` 的 SHA-256、候选 build-id、候选 release 验证结果
和独立回滚 release 验证结果。`status=passed` 才能进入后续切换评审。

## Worker 故障演练边界

该工具不证明 worker 数量，也不执行 worker replacement。当前 readiness 没有暴露稳定的
worker identity header，不能用重复 HTTP 请求推断四个 worker。四 worker 身份和 replacement
必须在独立、受控的本机演练中完成：先核验受管 master 的 PID/start ticks、命令行、端口和
release identity，再读取其直接子进程；只有强身份成立后，操作员才能按发布步骤替换一个
已选定 worker。HTTP 门禁可在 replacement 前后各执行一次，但不得承担发信号职责。
