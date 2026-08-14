# backend

## 职责

后端代码包含统一 FastAPI Web/API 应用（`api/`）、Agentic RAG 与检索/推理组件（`agentic_rag/`、`ai_search/`），以及后端测试。它负责鉴权、查询、助手、新闻/金融/图谱等 API 和数据访问。

不负责前端依赖或静态资源发布，也不把抓取、LLM 推理、批量加载当作普通开发启动项；这些任务由 `scripts/` 与受控 `deploy/` 流程管理。

## 主要入口

- API：在 `backend/` 目录使用 `api.main:app`（实现位于 `api/application.py`）。
- Agentic RAG：`agentic_rag/cli.py`、`agentic_rag/search_server.py` 及其 pipeline 模块；具体作业按脚本说明单独选择。
- 测试配置：仓库根目录 `pyproject.toml` 将测试目录设为 `backend/tests`，并提供 `gpu`、`integration`、`live_db`、`slow` 标记。

## 依赖与环境

后端统一依赖清单为 `backend/requirements.txt`（包含 `agentic_rag`、`api` 和 `ai_search` 依赖）；API 的最小清单在 `backend/api/requirements.txt`。需要 Python、FastAPI/Uvicorn、SQLAlchemy/PostgreSQL、Milvus；向量/LLM 路径还需要 PyTorch、sentence-transformers/vLLM 等模型运行时。

配置从环境文件读取，敏感值使用受控 secret 文件；不要提交 `.env`、数据库 URL、密码、token 或模型凭据。测试请使用 `APP_ENV=test` 和隔离数据库/fixture，不连接生产库。

## 开发与测试

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest backend/tests
```

按需排除需要外部服务的测试：`-m "not live_db and not integration and not gpu"`。静态检查以仓库 `pyproject.toml` 和受控质量门为准；不要把任意 pipeline 脚本当作 smoke test 执行。

## 数据与安全边界

后端读写 PostgreSQL/Milvus、工作区文件和模型服务。默认遵循最小权限、TLS/secret-file、请求限制与脱敏边界；不得在开发命令中写生产数据库、读取生产 release，或绕过 API 鉴权。数据库迁移、批量抓取、加载和长驻 worker 必须走 `deploy/` 运维审批及 checkpoint/回滚流程。

## 当前状态

主 API 与 Agentic RAG 均为活跃代码，但功能依赖外部数据库、Milvus、模型/LLM 服务和配置；后端测试既有纯单元测试，也有显式标记的集成/数据库/GPU 测试。该目录不是可脱离依赖的一键演示包。
