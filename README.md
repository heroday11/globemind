# GlobeMind — 地缘情报与舆情分析系统

从多语言新闻中提取地缘政治事件，通过 **L1 事件共指聚类 → L2 叙事线聚合** 形成结构化事件体系，并构建**四维涉华指数（CCI）**进行定量舆情分析。

## 管线概览

```
新闻语料 → v11 事件提取 (Qwen2.5-7B)
                  ↓
        L1 事件共指聚类 (BGE-M3 + FAISS + UnionFind)
                  ↓
            LLM 簇命名 → 写入 DB
                  ↓
          L2 Micro-Story 叙事线聚合
                  ↓
     ┌──────────────────────────────────┐
     ↓                                  ↓
  事件体系 (event_coref)        涉华分析管线
                              BGE-M3 + LR 涉华分类
                              GLiNER 实体 + LLM 情感/主题/框架
                              四维涉华指数 (CCI)
```

## L1 事件共指聚类

**核心文件：** `core_pipeline/event_coref_cluster.py`

输入为 v11 提取的地缘政治事件（9 类），基于 BGE-M3 1024 维嵌入进行相似度聚类：

1. 文章正文质量过滤（CSS 伪影、导航页、列表页检测）
2. 按 `event_type` 分区
3. FAISS 余弦近邻搜索 → mutual NN → 时间窗口（7-14 天）→ 极性检查
4. 适应性阈值：同实体对 0.75 / 单侧匹配 0.85 / 无共享实体 0.90
5. UnionFind 连通分量 → overlong 拆分 → impure 拆分

**质量指标（~30K 地缘文章）：** ~22K 簇，~15% 非单例，最大簇 ~54 篇

## L2 Micro-Story

在 L1 簇之上按时间轴和叙事关联聚合为故事线，供 Obsidian Vault 和前端展示。

## v11 事件提取

**核心文件：** `core_pipeline/event_extract_v11.py`

通过 Qwen2.5-7B-Instruct-AWQ（vLLM）提取三字段：`event_type` / `initiator` / `target`。

- 两步推理：先判 domain（geopolitical / general_news），再提取字段
- 19 统一类型（9 地缘 + 10 通用），general_news 时 initiator/target 为 null
- 输入截取 400 字符，max_tokens=80，支持断点续传 checkpoint

## 涉华分析管线

**核心文件：** `backend/agentic_rag/analysis_service.py`

### 阶段 ①a：涉华评分

```
新闻文本 → BGE-M3 编码 (1024维)
         → 逻辑回归 P(china|article) (ROC-AUC≈0.88)
         → 向量锚点余弦相似度兜底
         → china_related_index ∈ [0,1]
```

闸门：仅 `china_related_index ≥ 0.40` 的文章进入阶段 ①b。

### 阶段 ①b：精细分析

```
GLiNER 实体抽取 ──║── LLM 情感/主题/框架
                     │
                     ├ sentiment: 正面 / 负面 / 中立
                     ├ topic: 涉华核心主题（2-4 字）
                     └ frame: 中国威胁论/经济合作/军事冲突/人权批评
                             科技竞争/外交互动/国内治理/中立报道
```

### 阶段 ②：四维涉华指数 (CCI)

| 维度 | 名称 | 算法 |
|------|------|------|
| D1 | 注意力指数 | Σ(china_index) / N × 1000 |
| D2 | 情感效价 | 加权平均 sentiment（weighted by china_index） |
| D3 | 不确定性 | 涉华占比的滚动波动率（GPR 方法论） |
| D4 | 叙事分散度 | 1 - HHI（Herfindahl 逆指数） |

**复合指数：** `CCI = 0.25×D1_norm + 0.25×|D2_norm| + 0.25×D3_norm + 0.25×D4_norm`

所有维度支持按日/周/月聚合，可分解到话题级和框架级。

## 核心技术栈

| 模块 | 技术 |
|------|------|
| 事件提取 | Qwen2.5-7B-Instruct-AWQ @ vLLM |
| 语义嵌入 | BAAI/bge-m3（1024 维） |
| 事件聚类 | FAISS + sklearn + UnionFind |
| 涉华分类 | 逻辑回归（基于 BGE 嵌入训练） |
| 实体抽取 | GLiNER multi-v2.1 |
| 情感分析 | XLM-R ParlaSent / FinBERT |
| 数据库 | PostgreSQL（globemind / globemind_news）|
| 向量库 | Milvus |

## 运行管线

### L1 聚类 + L2 micro-story

```bash
python scripts/run_pipeline.py
```

### v11 事件提取（240K 采样）

```bash
python scripts/run_v11_240k.py
```

### 涉华分析全量

```bash
python -m agentic_rag.analysis_service --stage 1a --max-rows 50000
python -m agentic_rag.analysis_service --stage 1b
```

### 涉华指数聚合

```bash
python -m agentic_rag.china_index.aggregator
```

## 关键脚本

| 文件 | 用途 |
|------|------|
| `scripts/run_pipeline.py` | 完整管线（L1+L2） |
| `scripts/run_v11_240k.py` | 240K 文章 v11 提取 |
| `scripts/run_v11_cluster.py` | 独立 L1 聚类（从 DB 加载） |
| `scripts/eval_cluster_quality.py` | 聚类质量评估 |
| `scripts/eval_cluster_deep.py` | 深层聚类质量评估 |
| `scripts/eval_layer2_quality.py` | L2 叙事线质量评估 |
| `scripts/run_llm_naming.py` | LLM 簇命名 |

## 项目结构

```
core_pipeline/           # 核心聚类算法
├── event_extract_v11.py       # v11 事件提取
├── event_coref_cluster.py     # L1 事件共指聚类
└── union_find.py              # 并查集

backend/agentic_rag/     # 涉华分析后端
├── analysis_service.py         # 分析管线入口
├── china_index/                # 四维涉华指数
│   ├── learned_model.py        # 逻辑回归涉华分类
│   ├── china_anchors.py        # 向量锚点兜底
│   └── aggregator/             # 指数聚合
│       ├── composite.py        # CCI 合成
│       ├── d1_attention.py     # 注意力指数
│       ├── d2_polarity.py      # 情感效价
│       ├── d3_uncertainty.py   # 不确定性指数
│       └── d4_dispersion.py    # 叙事分散度
├── pipeline/                   # DB 导入管线
│   ├── event_coref_loader.py   # L1 写库
│   └── micro_story_builder.py  # L2 叙事线
└── db/                         # 数据库层

scripts/                 # 运行脚本
data/                    # 数据文件/checkpoint
config/                  # 配置
frontend/                # Vue / 金融终端前端
api/ → backend/api       # API 服务（symlink）
```
