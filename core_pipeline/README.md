# core_pipeline

## 职责

核心离线/批处理算法模块：事件抽取、实体规范化、事件共指聚类、主题聚类、事件演化链、文档对分类器和 Union-Find。模块接收文章/事件/向量等输入并产出分析结果，不是 HTTP API、前端或作业调度器。

## 主要入口

- `event_extract_v11.py`：调用 vLLM 的新闻事件抽取。
- `event_coref_cluster.py`：两层事件共指聚类。
- `event_evolution_chain.py`：从 L1 集群构建故事链，含 CLI `main()`。
- `topic_clustering.py`、`entity_normalizer.py`、`document_classifier.py`：可被脚本或 Python 调用的算法组件。
- `db_utils.py` 会直接连接 PostgreSQL/Milvus；除非明确需要，不要在本地验证中导入它。

## 依赖与环境

依赖来自仓库根 `requirements.txt`/后端统一环境，涉及 NumPy、scikit-learn、networkx/python-louvain、FAISS（可选）、PyTorch/模型和 vLLM。事件抽取默认读取 `VLLM_BASE_URL`，文档分类器默认查找 `data/models/` 下模型；数据库模块还需要安全的应用配置。

## 开发与测试

优先通过 `backend/tests` 或调用函数的隔离 fixture 验证；通用测试命令：

```bash
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest backend/tests -m "not live_db and not integration and not gpu"
```

模块中的 `__main__` 入口需要输入文件、模型服务或数据库，不应作为无参数的快速测试运行；批处理应由 `scripts/` 选择输入和 checkpoint 后执行。

## 数据与安全边界

处理新闻正文、实体、向量和模型输出，部分工具会写分析结果或数据库。输入应来自明确授权的 fixture/工作目录；不得把个人/生产数据、secret、模型凭据写进源码或提交物，也不得通过 `db_utils` 连接生产库做实验。

## 当前状态

事件抽取、分类和聚类算法同时存在可选依赖与兼容路径。模块适合被受控脚本复用，不提供单独稳定服务契约。
