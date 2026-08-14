# vue_project

## 职责

Vue 3/Vite 主站，包含搜索、新闻、研究工作区、助手、治理、故事图、金融终端导航等页面和前端状态/路由。它只负责浏览器呈现和 API 请求，不负责数据库、抓取、LLM 或发布 release。

## 主要入口

- `src/main.js` 创建 Vue 应用、Pinia 和 router。
- `src/App.vue` 与 `src/router/` 组织页面；`src/config/api.js` 管理 API 地址。
- `src/features/README.md` 是业务 feature、公共入口和聚焦测试索引。
- `scripts/build-release.mjs` 构建主站并整合金融终端；`scripts/dev-all.mjs` 是未接入默认命令的历史编排器。

## 依赖与环境

Node 版本要求见 `package.json`；依赖由 npm workspace 和仓库根 `package-lock.json` 锁定，请先在仓库根运行 `npm ci`。复制 `.env.example` 为本地环境配置，按需设置 `VITE_API_PROXY_TARGET`、`VITE_USE_API_MOCK`、`VITE_VLLM_PROXY_TARGET`；`VITE_*` 值会进入浏览器环境，严禁放入秘密。

## 开发与测试

```bash
npm run dev
npm run lint
npm run typecheck
npm run test:features
npm run build:main-only
```

以上命令在本目录执行。`npm run dev` 只启动 Vue 主站；`dev:main` 是兼容别名。构建输出可由 `VITE_OUT_DIR` 指向隔离目录，避免覆盖源文件或受控发布目录。
从仓库根目录执行 `make dev-web-mock` 可以使用有界本地 mock API 开始纯前端开发。

## 数据与安全边界

生产数据必须经后端 API 和现有鉴权/权限边界访问；mock API 脚本只用于本地 fixture。不要把 JWT、API key、数据库 URL 或内部主机写进 `VITE_*` 配置、静态资源或日志，也不要用前端脚本写生产数据库。

## 当前状态

主站是活跃 Vue 应用，包含较多历史兼容页面和 feature audit 文档；构建脚本还会整合金融终端静态包。开发、测试与发布产物边界必须保持分离。
