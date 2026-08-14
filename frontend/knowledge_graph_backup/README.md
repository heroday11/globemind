# knowledge_graph_backup

## 职责

知识图谱的兼容/占位单元，当前仅保留一个 `DashboardView.vue` 的展示 stub，用于兼容主站导航或构建目录结构。

它不是完整知识图谱产品：没有真实数据接入、完整路由、API、图计算或可用的开发服务器；不要把它当作生产图谱实现。

## 主要入口

- 唯一源码视图：`src/views/DashboardView.vue`。
- `package.json` 只有 `build` script；该 build 仅创建 `dist/` 目录，不编译或启动应用。

## 依赖与环境

当前 stub 无声明运行时依赖，也没有 `dev` script。主站若需要相关地址，应使用上层 Vue 项目的兼容配置，而不是假设本目录可独立开发。

## 开发与测试

只能核对文件和占位构建契约；已有命令为：

```bash
npm run build
```

该命令会写 `dist/`，不启动服务。不要执行 `npm run dev`（不存在），也不要为占位单元添加会连接生产数据的快捷脚本。

## 数据与安全边界

当前不应读取或写入业务数据库、Milvus、生产 API 或用户数据。未来接入图谱时必须先定义 API、权限、数据脱敏和测试 fixture 边界，不能把静态占位视为数据授权。

## 当前状态

非完整/兼容占位单元：只有 build stub，无 dev script，无完整实现。它已与 Vue 主站的默认开发命令分离。
