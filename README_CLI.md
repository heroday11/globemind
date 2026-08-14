# 舆情态势感知系统 — CLI 操作备忘录（Legacy / 并行路径）

> **文档角色**：本文描述以 `export_visuals.py`、`run_final_test.py`、UMAP + HDBSCAN 为主的**历史/并行**流水线。  
> **当前主路径**（PostgreSQL + Milvus + 宏观故事线 + `sync_obsidian_v4`）：请到仓库根目录阅读 **[`README.md`](README.md)**，命令入口为 `python -m agentic_rag.run_pipeline_stages`。  
> **虚拟环境**：一律在仓库根目录使用 `.venv`，激活方式见 **[`README.md`](README.md)#虚拟环境每次开工先做这一步**。  
> 最后更新：补充与主 README 对齐；正文为原第二阶段 CLI 说明。

---

## 黄金命令组合（日常使用）

### 命令一：全量刷新（最常用）
```bash
cd agentic_rag
python export_visuals.py --generate --analyze --min-size 10
```
**做了什么**：拉取 Milvus 所有向量 → UMAP 降维 → 生成散点图 JSON → 生成全部 Obsidian 笔记 → 并发 LLM 研判 → 写回 cluster_meta 表。

---

### 命令二：快速研判（跳过耗时的 UMAP，仅更新 LLM 分析）
```bash
cd agentic_rag
python export_visuals.py --analyze --min-size 15
```
**做了什么**：从 Milvus 轻量拉取 cluster 映射 → 跳过 UMAP 降维 → 只跑 LLM 舆情研判 → 写回 cluster_meta。
**适用场景**：Obsidian 笔记已生成，只想重跑分析结果。

---

### 命令三：聚类快速调参（不重灌向量，只重跑 HDBSCAN）
```bash
cd agentic_rag
python run_final_test.py --cluster-only
```
**做了什么**：跳过向量迁移（BGE-M3 embed + UMAP transform） → 直接读取 Milvus 中已有向量 → 重跑 HDBSCAN + 计算质心 + 路由测试。
**适用场景**：调整 `min_cluster_size` 等参数，快速验证聚类效果。

---

## 完整参数说明

### `export_visuals.py`

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--generate` | flag | — | 执行 UMAP 降维 + 生成散点图 JSON + 生成 Obsidian 笔记 |
| `--analyze` | flag | — | 批量 LLM 研判，写回 PostgreSQL `cluster_meta` 表 |
| `--min-size` | int | 10 | `--analyze` 时跳过规模不足的小簇 |
| `--workers` | int | 8 | `--analyze` 时的并发线程数 |

> **注意**：`--generate` 和 `--analyze` 至少选一个，否则打印帮助并退出。两者可同时使用。

### `run_final_test.py`

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--migrate-rows` | int | 10000 | 从 PostgreSQL 迁移到 Milvus 的向量行数 |
| `--reset` | flag | — | 删除 UMAP 模型 + 清空 Milvus collections，从零开始 |
| `--cluster-only` | flag | — | 跳过迁移，直接对已有向量重跑 HDBSCAN 聚类 |

---

## 新旧命令对照表

| 旧命令（已废弃） | 新命令 | 说明 |
|----------------|--------|------|
| `python export_visuals.py` | `python export_visuals.py --generate` | 生成散点图和 Obsidian 笔记 |
| `python export_visuals.py --analyze` | `python export_visuals.py --analyze` | 只跑 LLM 分析（不变）|
| `python export_visuals.py --skip-export --analyze` | `python export_visuals.py --analyze` | 废除 `--skip-export`，语义等价 |
| `python export_visuals.py --analyze --min-size 15` | `python export_visuals.py --analyze --min-size 15` | 不变 |
| `python export_visuals.py`（空跑，无意义）| 启动时打印帮助并报错退出 | 防止误操作 |
| `python run_final_test.py` | `python run_final_test.py` | 不变 |
| `python run_final_test.py --migrate-rows 50000` | `python run_final_test.py --migrate-rows 50000` | 不变 |
| _(不存在)_ | `python run_final_test.py --cluster-only` | 新增：快速聚类调参 |

---

## 完整业务流水线（从零到图谱）

```bash
# Step 1：训练 UMAP 模型（首次或重置后执行）
cd agentic_rag
python train_umap.py --samples 50000

# Step 2：向量迁移 + 聚类
python run_final_test.py --migrate-rows 50000

# Step 3：生成 Obsidian 笔记 + LLM 研判
python export_visuals.py --generate --analyze --min-size 10

# Step 4：同步到 Quartz 知识图谱站点
cd ..
python sync_to_quartz.py
```

---

## 目录结构速查

```
datasearch/
├── agentic_rag/
│   ├── export_visuals.py      # 主程序：生成 + 分析
│   ├── run_final_test.py      # 主程序：向量迁移 + 聚类
│   ├── train_umap.py          # 工具：训练 UMAP 模型
│   ├── benchmark_pipeline.py  # 工具：50k 基准测试
│   ├── tune_hdbscan_v2.py     # 工具：只读 HDBSCAN 验证
│   ├── tests/                 # 测试脚本目录
│   │   ├── test_full_pipeline.py
│   │   ├── test_milvus_conn.py
│   │   ├── test_ollama.py
│   │   └── validate_pg.py
│   ├── outputs/
│   │   ├── scatter_data.json          # 前端散点图数据
│   │   └── Obsidian_Vault/Events/     # 生成的 Obsidian 笔记
│   └── models/
│       └── umap_model.pkl             # 训练好的 UMAP 模型
├── dashboard/
│   ├── prep_data.py           # 生成 data.json
│   ├── gen.py                 # 生成 index.html
│   └── index.html             # 前端星图页面
├── sync_to_quartz.py          # Obsidian → Quartz 同步
├── quartz-site/               # Quartz 知识图谱站点
└── README_CLI.md              # 本文件
```
