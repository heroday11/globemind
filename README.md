# GlobeMind

GlobeMind 是一个面向全球新闻研究的地缘情报与舆情分析平台。它把新闻资料整理为可检索的事件、事件共指簇和叙事线，并提供涉华分析、证据链和研究型 API/前端能力。

仓库同时包含在线应用、离线分析模块和数据治理/运维工具。代码存在不等于真实数据、模型质量或生产发布已经验收；没有数据库、模型权重、外部服务和相应配置时，完整 AI 管线不能直接启动。

## 当前能力边界

- `backend/api` 提供 FastAPI 应用，入口为 `api.main:app`。
- `frontend/vue_project` 是当前主 Vue/Vite 前端；`frontend/financial-terminal` 是另一套前端，`frontend/knowledge_graph_backup` 仅作为知识图谱兼容占位/备份目录保留。
- `backend/agentic_rag` 包含涉华分析、检索、情感/实体处理、CCI 聚合及事件/故事线相关服务。
- `core_pipeline` 包含事件抽取与 L1 共指聚类等研究算法。
- 需要 PostgreSQL 才能提供完整 API 数据访问；部分检索/聚类路径还需要 Milvus 或本地替代配置。
- vLLM、嵌入模型、GLiNER、情感模型和外部 LLM/API 均需单独安装、下载、授权并配置。本仓库不把它们伪装成“零配置可用”的完整 AI 管线。

## Monorepo 结构

| 路径 | 职责 |
| --- | --- |
| `backend/api/` | FastAPI 应用、路由、认证、数据访问与服务层；开发入口 `api.main:app` |
| `backend/agentic_rag/` | 检索与涉华分析服务、事件/故事线处理、模型适配和 CCI |
| `backend/tests/` | Python 单元、契约、安全、架构和集成边界测试 |
| `core_pipeline/` | 事件抽取、事件共指聚类、实体规范化等离线算法 |
| `scripts/` | 数据质量、审计、导入、评估和维护脚本；使用前先阅读脚本说明与运行边界 |
| `frontend/vue_project/` | 当前主 Vue/Vite 应用及其前端测试 |
| `frontend/financial-terminal/` | 金融终端前端 |
| `frontend/knowledge_graph_backup/` | 知识图谱兼容占位/备份目录，不是当前独立应用入口 |
| `frontend/shared/` | 前端共享工具与类型 |
| `config/` | 应用设置、运行时环境清单和角色配置样例 |
| `data/` | 研究数据、来源目录和本地工作数据；不要把凭据或生产状态写入其中 |
| `deploy/` | 候选构建、浏览器 smoke 和运行控制工具；不是日常开发入口 |
| `docs/` | 当前开发/架构/运维参考与历史证据，见 [`docs/README.md`](docs/README.md) |

## 前置条件

- Python 3.11；建议使用仓库外或仓库内的隔离虚拟环境。
- Node.js 22 与 npm。主前端的 `engines` 也接受 Node 20.19+，新开发统一按 Node 22 验证。
- PostgreSQL 及一个供本地开发使用的数据库和账号。数据库 schema、运行时角色和凭据文件按 [`config/runtime/README.md`](config/runtime/README.md) 与 API 环境样例配置。
- 若要运行模型或向量路径，还需要相应的 GPU/CPU 资源、模型权重、Milvus（或明确配置的本地替代）以及外部服务凭据。

## 安全的本地快速开始

以下只启动本地开发服务，不启动抓取、长时间 AI 管线或生产进程。

1. 安装依赖并准备本地环境：

   ```bash
   cd /path/to/globemind
   python3.11 -m venv .venv
   . .venv/bin/activate
   PYTHONDONTWRITEBYTECODE=1 python -B -m pip install -r requirements-dev.txt
   npm ci --prefix frontend/vue_project
   ```

   需要离线分析/模型路径时，再根据目标模块和资源情况选择性安装根目录 `requirements.txt`；其中包含重量级模型依赖，不是主前端/API 的必需最小安装。

2. 准备 API 环境。仅在文件不存在时复制样例，然后编辑 `backend/api/.env`，填入本地 PostgreSQL 连接信息及必要的开发配置；不要提交 `.env`、密码、token 或模型密钥。

   ```bash
   test -e backend/api/.env || cp backend/api/.env.example backend/api/.env
   ```

3. 在一个终端从 `backend` 目录启动 API：

   ```bash
   cd backend
   PYTHONDONTWRITEBYTECODE=1 python -B -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8088
   ```

   API 启动会读取环境配置并检查数据库可用性；数据库或必需 schema 未准备好时，服务不会变成一个“假可用”的完整后端。

4. 在另一个终端从仓库根目录启动主前端：

   ```bash
   npm --prefix frontend/vue_project run dev:main
   ```

   `npm --prefix frontend/vue_project run dev` 是同一主站开发入口，`dev:main` 作为兼容别名保留。按前端环境样例设置 API 代理（通常指向 `http://127.0.0.1:8088`）。如果只是开发没有后端，可明确使用前端 mock；mock 不代表后端或 AI 管线已工作。

## 测试与质量门禁

在仓库根目录、已激活 Python 3.11 虚拟环境中运行：

```bash
# Python 测试（配置见 pyproject.toml，默认收集 backend/tests）
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -q

# 主前端 lint、特性测试和构建
npm --prefix frontend/vue_project run lint
npm --prefix frontend/vue_project run test:features
npm --prefix frontend/vue_project run build:main-only

# 仓库当前受控门禁（含已纳入基线的 Python Ruff 目标）
PYTHONDONTWRITEBYTECODE=1 PYTHON_BIN=python deploy/run_quality_gate.sh
```

当前 Python lint 采用受控目标清单逐步扩展；全仓历史代码尚未达到一次性全量 Ruff 清零。部分测试带有 `integration`、`live_db`、`gpu` 或 `slow` 标记，需要额外服务、数据或硬件；不要为了让门禁变绿而跳过其前置条件。涉及发布、运行时清单、浏览器 smoke 或候选环境的检查，先阅读 [`docs/operations/`](docs/operations/) 中对应 runbook。质量门禁通过也不等于真实数据覆盖、模型准确率、许可或生产发布已经批准。

## 文档导航

- 开发者入口与文档分类：[`docs/README.md`](docs/README.md)
- 架构模块图：[`docs/architecture/module-map.md`](docs/architecture/module-map.md)
- 开发整理路线：[`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md)
- API/运行时配置：[`config/runtime/README.md`](config/runtime/README.md)
- Python 运行时：[`docs/operations/PYTHON_RUNTIME.md`](docs/operations/PYTHON_RUNTIME.md)
- 发布边界：[`docs/operations/RELEASES.md`](docs/operations/RELEASES.md)
- 运行控制：[`docs/operations/RUNTIME_CONTROL.md`](docs/operations/RUNTIME_CONTROL.md)
- 安全贡献与漏洞报告：[`SECURITY.md`](SECURITY.md)
- 历史 CLI 说明：[`README_CLI.md`](README_CLI.md)（仅归档，不是当前入口）

## 生产边界

生产 release 是不可随意修改的证据和部署边界。不得运行或导入 `/root/data/releases/globemind/current`、任何版本化 release、`previous` 或 `rejected` 中的 Python，也不得把 release 的 `backend` 加入 `PYTHONPATH`。发布相关工作必须在源码仓库或隔离 staging 副本中完成，并遵循 [`AGENTS.md`](AGENTS.md) 和 [`docs/operations/RELEASES.md`](docs/operations/RELEASES.md) 的校验、原子提升和回滚要求。

不要依据 PID 文件或命令名操作服务，不要在没有 checkpoint、回放证明、回滚方案和明确维护步骤时停止、重启、接管或迁移长管线。开发者快速开始不授权任何生产操作。
