# ai_search

`ai_search` 是可安装的研究检索包，提供本地搜索引擎和研究代理的稳定模块入口。

```python
from ai_search.research_agent import ResearchRunner
from ai_search.search_engine import LocalSearchEngine
```

命令行入口使用 `python -m ai_search.run_query`。导入包不会启动 SearXNG、数据库、
模型或网络服务；调用 `ResearchRunner` 时由调用方显式配置搜索和模型依赖。

生产代码不得依赖脚本目录或运行时 `sys.path` 注入。需要安装开发环境时，在仓库根目录
执行 `pip install -e .`，即可获得 `api`、`agentic_rag`、`ai_search`、`runtime_control`、
`core_pipeline` 和 `config` 包。
