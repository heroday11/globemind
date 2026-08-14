# Agentic RAG — 涉华舆情分析后端

本包实现 GlobeMind 系统的涉华分析管线，包含 China Relevance Index (CCI) 的完整计算流程。

## 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| 分析管线入口 | `analysis_service.py` | Stage 1a (BGE+LR 涉华评分), Stage 1b (GLiNER+LLM 细分析), Stage 44 (Milvus 同步) |
| 实体抽取 | `gliner_extractor.py` | GLiNER multi-v2.1 多语言命名实体识别 |
| 情感分析 | `stage1b_sentiment.py` | ParlaSent/HF 情感模型，支持 OOM 自适应缩批 |
| 簇命名 | `naming_service.py` | LLM 为事件簇生成中文名称 |
| Milvus 同步 | `sync_china_news_to_milvus()` in `analysis_service.py` | 向量化新闻 → Milvus 增量路由 |
| 涉华指数 | `china_index/` | 四维 CCI 聚合 (D1-D4) |

## 管线命令

```bash
cd /root/data/globemind

# Stage 1a: BGE+LR 涉华评分
python -m agentic_rag.analysis_service --stage 1a --max-rows 50000

# Stage 1b: 细分析 (GLiNER + 情感/主题/框架)
python -m agentic_rag.analysis_service --stage 1b

# Stage 44: Milvus 增量同步
python -m agentic_rag.analysis_service --stage 44

# CCI 指数聚合
python -m agentic_rag.china_index.aggregator
```

## 配置

所有配置通过环境变量或 `.env` 文件设置，详见 `.env.example` 和项目根目录 `README.md`。
