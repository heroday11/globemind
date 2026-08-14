# remotion-edit

Status: current isolated demo tooling
Scope: local Remotion source, render commands and media-input boundary

## 职责

独立的 Remotion 视频演示编辑工具，用于把已有 GlobeMind 产品录屏组合成带注释的演示视频。它不是 Vue 主站、API、数据管线或生产发布的一部分。

## 入口与依赖

- `src/index.tsx` 注册 Remotion composition；`src/GlobemindDemo.tsx` 定义当前演示时间线。
- 依赖由本目录 `package-lock.json` 锁定，使用 Node.js 和 npm。
- `scripts/` 中的语音/混音辅助工具需要额外 Python 和媒体依赖，应在隔离环境中按脚本参数使用。

## 本地开发

```bash
npm ci --prefix remotion-edit
npm --prefix remotion-edit run preview
```

确认输入素材和授权后，可用 `npm --prefix remotion-edit run render` 写入本地 `remotion-edit/out/`。`audio/`、`out/`、`public/source.mp4`、本地虚拟环境和依赖目录均是外部输入或生成物，不应提交。

## 安全边界

录屏可能包含用户数据、内部地址、token 或未公开界面；导入素材前必须脱敏。不要从生产服务自动抓取画面，不要把本模块的输出当作发布验收证据，生成视频也不得写入 release 目录。
