# financial-terminal

## 职责

独立 React/Vite 金融终端，提供终端仪表盘、数值分析告警及其 trust/triage 逻辑。它是前端展示单元，不负责行情采集、数据库写入、模型服务或生产部署。

## 主要入口

- `src/main.tsx` 启动 React；`src/App.tsx` 定义 `/` 和 `/numerical-alert` 路由。
- API 封装在 `src/api/`，业务判断在 `src/lib/` 和 `src/hooks/`。

## 依赖与环境

需要 Node.js、React 18、TypeScript、Vite；依赖由 npm workspace 和仓库根 `package-lock.json` 锁定。API 地址和 mock 行为应通过本地环境配置，浏览器端变量不得包含凭据。

## 开发与测试

```bash
npm ci # 在仓库根目录执行
npm run dev
npm run typecheck
npm run lint
npm test
npm run test:trust
npm run build
```

`build` 默认写本地 `dist/`；被主站整合时由 `vue_project/scripts/build-release.mjs` 使用隔离输出目录。`dev`/`preview` 仅用于本地浏览器验证，不是生产启动方式。

## 数据与安全边界

行情、告警和信任状态应通过受控 API/fixture 获取；`src/lib/mockData.ts` 仅为本地开发数据。不得把 API key、数据库连接串或用户敏感信息打包进前端，也不得直接从浏览器连接生产数据库。

## 当前状态

金融终端为可独立构建和测试的 React 单元，并可作为 Vue 主站的静态子应用；真实数据可用性仍依赖后端 API 和相应权限。
