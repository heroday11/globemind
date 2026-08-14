# GlobeMind 当前交接入口

## 当前事实

- 当前生产 `current` 是不可变 V0.11 build
  `/root/data/releases/globemind/0.11.0-20260710T223243Z`，匹配 Web runtime
  `/root/data/python-runtimes/globemind-web/0.11.0`。
- 上述 V0.11 release/runtime 是 V1 晋级前必须重新验证的生产基线，也是 V1 的明确回滚
  锚点。现有 `previous` 仍指向 V0.10 历史 release；在 V1 两阶段 promotion 成功前不要
  手工改写任一链接。
- 工作区源码已将唯一版本源设为 `1.0.0`，正在执行正式 V1 门禁、运行时和候选发布
  步骤；生产仍是 V0.11。源码状态、`VERSION` 和线上版本是三个不同事实，不能据其中
  一个推断另两个。
- 生产 Web、Cloudflare 和持续管线不得因源码收口或文档核对而重启。

## 当前操作入口

后续会话先阅读仓库根目录的 `AGENTS.md`，再依次阅读：

1. [V1_RELEASE_CHECKLIST.md](V1_RELEASE_CHECKLIST.md)
2. [RELEASES.md](RELEASES.md)
3. [PYTHON_RUNTIME.md](PYTHON_RUNTIME.md)
4. [V1_WEB_PROMOTION.md](V1_WEB_PROMOTION.md)
5. [RUNTIME_CONTROL.md](RUNTIME_CONTROL.md)

所有候选身份从 `VERSION` 派生，所有生产/回滚身份从选定不可变 release 的
`release.json` 派生。不要把旧文档里的版本字面量复制到新命令。

## 历史证据

- [HANDOFF_20260711_0029_V010_CHECKPOINT.md](HANDOFF_20260711_0029_V010_CHECKPOINT.md)
- [HANDOFF_20260711_V1.md](HANDOFF_20260711_V1.md)
- [V011_ACCEPTANCE.md](V011_ACCEPTANCE.md)

带日期的 handoff、旧 acceptance 和 incident 文档保留当时事实，不是当前操作说明。
