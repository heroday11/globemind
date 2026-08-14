# Historical Crawl Handoff

更新时间：`2026-06-22 19:03 CST`

## 任务目标

当前主要任务是为 `GlobeMind` 的政治新闻分析系统抓取一批高质量新闻站点的近一年历史新闻，保留：

- 标题
- 正文
- 发布时间
- 作者
- 来源站点
- URL

当前运行的是：

- `wave1` 高质量新闻源集合
- 时间范围：`2025-06-21` 到 `2026-06-20`
- 当前先跑 `1 年`
- 目标是后续再评估是否扩到 `3 年`

## 当前运行批次

- `run_id`: `wave1_1y_prod_20260621`
- job 目录：
  - [/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621](/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621:1)

## 当前状态

截至本文档更新时间：

- 总待处理 URL：`3,478,673`
- 已处理：`311,276`
- 成功提取：`259,938`
- 失败：`51,338`
- 剩余：`3,167,403`
- 当前完成度：约 `8.95%`

数据库装载状态：

- loader 已看到：`259,899`
- 已插入：`257,201`
- skipped：`2,698`

## 关键进程

当前应当看到这三个进程：

1. extractor supervisor
   - PID：`107956`
   - 命令：`python3 /root/data/globemind/scripts/supervise_adaptive_extractor.py ...`
2. extractor child
   - PID：`107974`
   - 命令：`/root/data/globemind/.env_torch/bin/python /root/data/globemind/scripts/adaptive_global_extractor.py ...`
3. postgres loader
   - PID：`14176`
   - 命令：`python3 /root/data/globemind/scripts/stream_load_news_to_postgres.py ...`

不要误杀这三个进程。

## 为什么之前停过

之前停住的根因不是网络慢，而是：

- 旧的 extractor 是“裸跑进程”
- 退出后没有 watchdog 自动恢复
- 所以看起来像“系统还活着，但数字不再增长”

现在已经补上守护器：

- 新增脚本：
  - [/root/data/globemind/scripts/supervise_adaptive_extractor.py](/root/data/globemind/scripts/supervise_adaptive_extractor.py:1)

这个守护器负责：

- 拉起 extractor
- 监控 progress 文件是否长期不更新
- 子进程异常退出时自动 `resume`
- 记录 supervisor 状态和重启次数

补充：`2026-06-22 17:21` 左右曾出现 supervisor 进程消失、child 变成 orphan 但仍继续抓取的情况。`2026-06-22 19:01 CST` 已停掉 orphan child，并用 `setsid -f` 方式重新启动 supervisor，避免当前会话退出时清理掉 supervisor 父进程。

## 当前关键文件

### 输入队列

- 原始合并队列：
  - [/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/wave1_discovered_urls_merged.jsonl](/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/wave1_discovered_urls_merged.jsonl:1)
- 剪枝后队列：
  - [/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/wave1_discovered_urls_merged_pruned.jsonl](/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/wave1_discovered_urls_merged_pruned.jsonl:1)

### 提取输出

- 成功文章：
  - [/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/wave1_articles_merged.jsonl](/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/wave1_articles_merged.jsonl:1)
- 错误文章：
  - [/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/wave1_articles_merged_errors.jsonl](/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/wave1_articles_merged_errors.jsonl:1)

### 进度与状态

- extractor progress：
  - [/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/wave1_articles_merged_progress.json](/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/wave1_articles_merged_progress.json:1)
- loader state：
  - [/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/news_loader_state.json](/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/news_loader_state.json:1)
- supervisor state：
  - [/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/extractor_supervisor_state.json](/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/extractor_supervisor_state.json:1)
- supervisor heartbeat：
  - [/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/extractor_supervisor_heartbeat.json](/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/extractor_supervisor_heartbeat.json:1)

### 日志

- supervisor 日志：
  - [/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/logs/extractor_supervisor.log](/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/logs/extractor_supervisor.log:1)
- extractor stdout：
  - [/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/logs/extractor_stdout.log](/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/logs/extractor_stdout.log:1)
- loader 日志：
  - [/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/logs/stream_loader.log](/root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/logs/stream_loader.log:1)

## 常用检查方法

### 1. 看核心进程是否还活着

```bash
pgrep -af 'supervise_adaptive_extractor.py|adaptive_global_extractor.py|stream_load_news_to_postgres.py'
```

### 2. 看 supervisor 状态

```bash
cat /root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/extractor_supervisor_state.json
```

重点看：

- `status`
- `restart_count`
- `child_pid`

### 3. 看进度是否在增长

```bash
wc -l \
  /root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/wave1_articles_merged.jsonl \
  /root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/wave1_articles_merged_errors.jsonl
```

隔一两分钟再看一次，如果数字增长，说明抓取还在继续。

### 4. 看当前进度 JSON

```bash
cat /root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/wave1_articles_merged_progress.json
```

注意：

- `running` 字段不完全可靠
- 更可靠的判断方式是：
  - 进程是否存活
  - progress 文件 mtime 是否更新
  - jsonl 行数是否增长

### 5. 看 loader 是否跟上

```bash
cat /root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/news_loader_state.json
```

重点看：

- `seen`
- `inserted`
- `skipped`

### 6. 看最近日志

```bash
tail -n 50 /root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/logs/extractor_supervisor.log
tail -n 50 /root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/logs/extractor_stdout.log
```

## 当前运行参数

当前 extractor child 主要参数：

- `--resume`
- `--global-concurrency 16`
- `--max-per-domain 4`
- `--min-per-domain 1`
- `--timeout 20.0`
- `--retry-limit 2`
- `--shuffle`
- `--proxy-pool /root/data/globemind/data/proxy_pool/proxy_pool_manifest_refreshed_20260622.json`

当前 supervisor 参数：

- `--heartbeat-interval-sec 30`
- `--stale-progress-sec 900`
- `--restart-delay-sec 5`

含义：

- 如果 15 分钟没有进度更新，supervisor 会判断为 stale 并重启 child
- child 重启时会自动 `resume`

## 速度和 ETA 的理解

当前抓取不是按日期顺序跑，而是 `shuffle` 后的全年混合抓取。

所以：

- 不能说“已经跑到某个具体日期”
- 只能按总量比例折算“相当于一年里跑了多少天的数据量”

最近估算口径：

- 按墙钟平均速度：大约还要 `18-19 天` 跑完这一年
- 按当前抓取稳定阶段速度：大约还要 `15-20 天`

## 当前错误热点

当前失败最多的站点包括：

- `who_int`
- `asean_news`
- `africanews`
- `ukrinform`
- `ecowas_news`

这些站点的失败原因多为：

- `missing_core_fields`
- `http_406`
- `Server disconnected`
- `General SOCKS server failure`

这说明：

- 有一部分是目标站内容结构问题
- 有一部分是反爬/响应质量问题
- 有一部分是代理节点质量波动

## 资源占用结论

最近一次检查结果：

- CPU 整机空闲很多，抓取器主要占 `1` 个核
- 内存非常充足
- GPU 基本空闲
- 磁盘空间充足

所以：

- 可以在这台机器继续做别的事
- 但不要再开新的大规模网络爬虫或重 I/O 落盘任务
- 不要动当前的 proxy pool 进程

## 如果新会话想快速接手

把这份文档发给新会话，然后让它先做这几步：

1. 读本文档
2. 查看这三个文件：
   - `extractor_supervisor_state.json`
   - `wave1_articles_merged_progress.json`
   - `news_loader_state.json`
3. 跑一次：

```bash
pgrep -af 'supervise_adaptive_extractor.py|adaptive_global_extractor.py|stream_load_news_to_postgres.py'
wc -l /root/data/globemind/data/historical_news/jobs/wave1_1y_prod_20260621/wave1_articles_merged.jsonl
```

4. 再根据是否增长，判断当前是否健康运行

## 额外补充：静态网页部署

`globemind.top` 当前临时切成了静态入口，不是原业务前端。

可用页面：

- `https://globemind.top/`
- `https://globemind.top/xigai/`
- `https://globemind.top/english-lit/`

相关配置：

- [/etc/nginx/conf.d/globemind-tunnel.conf](/etc/nginx/conf.d/globemind-tunnel.conf:1)
- [/root/data/cloudflared/config.yml](/root/data/cloudflared/config.yml:1)
