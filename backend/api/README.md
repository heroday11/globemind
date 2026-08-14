# backend/api

## 职责

统一 FastAPI 应用层：挂载认证、搜索、助手、新闻、金融、图谱、治理、运维等路由，提供服务层、ORM、数据库/Milvus 适配和请求安全中间件。不负责抓取或全量特征/聚类作业，也不负责发布生产 release。

## 主要入口

- ASGI 对象：`api.main:app`（转发到 `api.application.app`）。
- 路由：`routes/`；业务服务：`services/`；运行时和安全配置：`core/`。
- Feature 公共入口与首选测试：[`features/README.md`](features/README.md)。
- 容器入口记录在 `Dockerfile`；容器构建/部署须由运维流程执行。

## 依赖与环境

依赖见 `requirements.txt`，包括 FastAPI/Uvicorn、Pydantic、SQLAlchemy/psycopg2、PyJWT、Milvus、HTTP 客户端及可选 LLM/向量包。复制 `.env.example` 为本地环境文件并按需填写；数据库密码应由 `GLOBEMIND_DB_PASSWORD_FILE` 等 secret-file 提供，`.env` 不得提交。生产文档和 OpenAPI 暴露受 `APP_ENV` 控制。

## 开发与测试

在仓库根目录运行后端测试：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest backend/tests
```

API 代码的 smoke 脚本位于 `api/scripts/`，仅在隔离的测试配置、mock/fixture 或明确授权的测试服务上运行；不要把迁移脚本和 `smoke_*` 当作生产健康检查快捷命令。需要本地 HTTP 调试时，先准备 `APP_ENV=test` 和非生产依赖，再按项目运行手册显式启动 Uvicorn。

## 数据与安全边界

应用会访问 PostgreSQL 的 `news`/用户等表、Milvus 和工作区文件，并处理身份凭据、上传内容和 LLM 请求。禁止在 README 命令或测试配置中硬编码密钥、连接串或生产主机；禁止绕过鉴权、运行时 schema 变更保护或请求/上传限制。生产库迁移只能由受控运维步骤完成。

## 当前状态

这是当前统一 API 实现，仍包含兼容路由和多个特性模块；接口可用性取决于数据库 schema、secret、Milvus/LLM 等外部依赖。新增逻辑应进入明确的路由、服务和模型模块，不应依赖本地备份文件。
