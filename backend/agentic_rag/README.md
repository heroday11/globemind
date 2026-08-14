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
cd /path/to/globemind
PYTHONDONTWRITEBYTECODE=1 python -B -m pip install -e .

# Stage 1a: BGE+LR 涉华评分
PYTHONDONTWRITEBYTECODE=1 python -B -m agentic_rag.analysis_service --stage 1a --max-rows 50000

# Stage 1b: 细分析 (GLiNER + 情感/主题/框架)
PYTHONDONTWRITEBYTECODE=1 python -B -m agentic_rag.analysis_service --stage 1b

# Stage 44: Milvus 增量同步
PYTHONDONTWRITEBYTECODE=1 python -B -m agentic_rag.analysis_service --stage 44

# CCI 指数聚合
PYTHONDONTWRITEBYTECODE=1 python -B -m agentic_rag.china_index.aggregator
```

## 配置

所有配置通过环境变量或 `.env` 文件设置，详见 `.env.example` 和项目根目录 `README.md`。

## Python 公共入口契约

这是可安装的 `agentic_rag` 包。应用代码应通过包入口或明确的模块入口调用，
不要依赖当前工作目录或修改 `sys.path`：

```python
from agentic_rag.ingestion.pipeline import IngestionPipeline
from agentic_rag.china_index.aggregator import composite_china_index
```

CLI 使用 `python -m agentic_rag.cli`；导入包本身只解析路径和配置，不启动数据库、
Milvus 或模型服务。实际管线调用仍需显式提供相应依赖和凭据。
