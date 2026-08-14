# frontend

## 职责

前端工作区包含 Vue 主站（`vue_project/`）、React 金融终端（`financial-terminal/`）、知识图谱兼容单元（`knowledge_graph_backup/`）和少量共享配置。它负责浏览器界面、路由、展示状态和 API 调用，不负责数据库写入、新闻抓取、模型推理或生产发布。

## 主要入口

- 根 `package.json` 仅做子项目脚本转发。
- Vue 主站入口：`vue_project/src/main.js`；金融终端入口：`financial-terminal/src/main.tsx`。
- 生产构建编排：`vue_project/scripts/build-release.mjs`；该脚本输出应指向隔离构建目录。

## 依赖与环境

需要 Node.js（Vue 项目要求 `^20.19.0 || >=22.12.0`）及 npm lockfile。安装依赖使用 `npm ci --prefix vue_project` 和 `npm ci --prefix financial-terminal`；API/vLLM 地址通过各子项目 `.env.example` 配置。不要提交 `.env*` 中的密钥或把内部服务凭据打入浏览器包。

## 开发与测试

```bash
npm run dev --prefix frontend/vue_project
npm run build:main-only --prefix frontend/vue_project
npm run build --prefix frontend/financial-terminal
npm run test:trust --prefix frontend/financial-terminal
```

Vue 的默认 `npm run dev` 只启动主站；知识图谱兼容单元没有开发服务器。构建只写本地输出目录，不等同于生产发布；发布必须走 `deploy/` 的受控流程。

## 数据与安全边界

前端只应通过 API 获取业务数据；mock 数据、fixture 和本地静态资源不得伪装成生产数据。禁止在客户端源码、source map 或构建产物写入 token、数据库连接信息或 secret；不要直接调用生产数据库。`dist/`、`node_modules/` 是构建/依赖产物，不是业务源文件。

## 当前状态

Vue 主站和金融终端为两个可独立构建的应用，主站还包含金融终端静态集成与知识图谱导航；共享契约仍依赖后端 API 配置。兼容知识图谱单元不应被视为完整产品模块。
