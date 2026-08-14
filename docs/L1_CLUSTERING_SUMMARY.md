# Globemind L1 + L2 聚类管线（v13 正式版）

## 概述

Globemind 管线从多语言新闻中提取地缘政治事件，通过多信号加权融合算法聚类为事件级簇，再通过实体对+动作转换分析构建故事级演化图。

## 架构

```
┌──────────────────────────────────────────────────────────────┐
│                    全量管线总览                                │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  240K 新闻                                                     │
│    ↓ ① 域分类器 (TF-IDF+LR, 0.3s)                             │
│  ~58K 候选人                                                   │
│    ↓ ② vLLM 提取 (Qwen2.5-7B, 7字段, ~26min)                  │
│  25K geopolitical + 215K general_news                         │
│    ↓ ③ 话题先验 (TF-IDF+Louvain, 12-13话题)                    │
│    ↓ ④ 5信号融合聚类 (BGE-M3 0.40+实体 0.30+时间 0.15         │
│                      +trigger 0.10+location 0.05)              │
│    ↓                                                          │
│  ┌──────────────────┐                                         │
│  │ DB: 18K L1 簇    │──→ 映射文件                              │
│  │ 1.5K非独居+16.5K │                                         │
│  └────────┬─────────┘                                         │
│           ↓ ⑤ L2: Event Evolution Chain (1.9s)                │
│  ┌──────────────────┐                                         │
│  │ DB: 95 故事图     │                                         │
│  │ 2.5K 边, 6种类型  │                                         │
│  │ escalation 8%    │                                         │
│  └──────────────────┘                                         │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

## 核心文件

| 文件 | 说明 |
|:-----|:------|
| `core_pipeline/event_extract_v11.py` | vLLM 提取：7 字段（domain/event_type/initiator/target/trigger_verb/location/tone） |
| `core_pipeline/event_coref_cluster.py` | L1 聚类主算法：5 信号加权融合 + UnionFind + 后处理 |
| `core_pipeline/topic_clustering.py` | TF-IDF 关键词图 + Louvain 话题先验 |
| `core_pipeline/union_find.py` | 并查集实现连通分量 |
| `core_pipeline/entity_normalizer.py` | 实体名规范化（别名映射、头衔剥离、跨语言处理） |
| `scripts/run_event_level_pipeline.py` | 完整管线入口：checkpoint → 话题 → L1 → DB |
| `scripts/extract_all_v12.py` | 全量提取脚本：分类器预筛选 → LLM 提取 → 合并 |
| `data/models/domain_classifier_lr.joblib` | 域分类器（TF-IDF + LogisticRegression） |
| `data/models/domain_tfidf_lr.joblib` | 域分类器 TF-IDF 向量化器 |

## 提取阶段

### 域分类器预筛选

- **模型**: LogisticRegression on TF-IDF (30K features, (1,3)-gram)
- **训练数据**: v12 标注的 23,477 geopolitical + 70,431 general_news
- **阈值**: 0.30
- **严格 v13 口径**: Recall 98.3%, Precision 42.3%, F1 59.2%
- **业务复评口径**: L1 事件 Precision 约 51.4%；地缘相关素材池（event+context）Precision 约 77.4%
- **作用**: 从 240K 篇文章中筛选出 58,504 篇候选，减少 LLM 调用量约 75.6%

### LLM 提取

- **模型**: Qwen2.5-7B-Instruct-AWQ (via vLLM, port 8004)
- **输入**: 标题 + 正文前 400 字
- **输出**: 7 字段 JSON
  - `domain`: geopolitical / general_news
  - `event_type`: 9 种地缘政治 + 9 种通用新闻
  - `initiator`: 动作发起方（去掉头衔）
  - `target`: 动作承受方（去掉头衔）
  - `trigger_verb`: 核心动作短语（从正文中提取）
  - `location`: 事件发生地（国家名优先）
  - `tone`: 语气（positive/negative/neutral）
- **并发**: 200（A30 24GB 安全值）
- **速度**: ~18 篇/秒

## 聚类阶段

### 5 信号加权融合

```python
FUSION_WEIGHTS = {
    "bge": 0.40,       # BGE-M3 嵌入余弦相似度
    "entity": 0.30,    # 实体名软相似度（canonical归一化 + last-token对比）
    "time": 0.15,      # 时间衰减（指数函数）
    "trigger": 0.10,   # trigger_verb 相似度（token Jaccard）
    "location": 0.05,  # 地点相似度（canonical归一化 + trigram）
}
```

### 预过滤（硬门禁）

- 时间窗口：7 天（diplomacy/trade/policy/human_rights/aid）/ 3 天（其他）
- 互逆最近邻：防止传递链
- 语气极性：positive vs negative 不合并

### 自适应阈值

```python
_alignment = (entity_score + trigger_score) / 2.0
if _alignment >= 0.8:       thresh = 0.70
elif _alignment >= 0.3:     thresh = 0.75
else:                       thresh = 0.80
```

### 后处理

- `split_overlong_clusters`: 贪心拆分跨度过长的簇
- `split_impure_clusters`: 拆分实体对过多的簇（max_entity_pairs=4）

## 性能数据

### 聚类效果

| 指标 | 值 |
|:-----|:---:|
| 总文章 | 240,160 |
| 参与聚类文章 | 21,951 |
| Geopolitical 文章 | 25,198 (10.5%) |
| 总簇数 | 18,480 |
| 非独居簇 | 1,478 (8.0%) |
| 文章维度非独居率 | ~22.5% |
| 最大簇 | 53 篇 |
| 事件级纯度 | 100%（0簇>7天） |
| L1 时间 | 33s |

### 运行时间

| 阶段 | 时间 |
|:-----|:----:|
| 域分类器（240K 篇） | 45s |
| LLM 提取（~29K 篇） | 26min |
| L1 聚类（21K 篇） | 33s |
| **总计** | **~27min** |

## 数据文件

| 文件 | 说明 |
|:-----|:------|
| `data/checkpoint_v13_all.jsonl` | 全量 240K 篇文章的 7 字段提取结果 |
| `data/checkpoint_v12_geopolitical.jsonl` | v12 地缘政治子集（32K 篇，含 7 字段） |
| `data/event_coref_mapping_layer1.jsonl` | L1 聚类映射（cluster_id → article_id） |
| `data/models/domain_classifier_lr.joblib` | 域分类器模型 |

## DB 表

| 表 | 说明 |
|:----|:------|
| `event_coref_clusters` | 簇信息（cluster_id, article_count, event_type, initiator, target） |
| `event_coref_members` | 簇成员映射（cluster_id, news_id） |

## 关键代码段

### 实体软相似度

```python
def _entity_similarity(name1, name2):
    """三层级：canonical归一化 → last-token对比 → trigram回退。"""
    c1 = _canonical_entity(name1) or name1.lower()
    c2 = _canonical_entity(name2) or name2.lower()
    if c1 == c2: return 1.0
    last1 = c1.split()[-1]; last2 = c2.split()[-1]
    if last1 == last2: return 1.0
    tri = _trigram_jaccard(last1, last2)
    if len(last1) < 8 and len(last2) < 8:
        tri *= 0.5  # 短名惩罚
    return tri
```

### 融合分数

```python
_fusion_score = (
    FUSION_WEIGHTS["bge"] * cos
    + FUSION_WEIGHTS["entity"] * _entity_score
    + FUSION_WEIGHTS["time"] * _time_score
    + FUSION_WEIGHTS["trigger"] * _trigger_score
    + FUSION_WEIGHTS["location"] * _location_score
)
```

## 迭代历史

| 版本 | 方法 | 非独居率 |
|:-----|:-----|:--------:|
| 原始 | 硬阈值 0.75/0.85/0.90 | 6.9% |
| R1 | 多信号融合 | 9.3% |
| R3 | +阈值调优 | 10.3% |
| v12 | +trigger_verb/location/tone | 8.0% |
| **v13** | **+域分类器预筛选+全量提取** | **8.0%** |

> 注：v12/v13 数据更干净（去除了 v11 的 ~9K 误报），非独居率数值略低但质量更高。
