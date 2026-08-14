"""
PostgreSQL 安全执行器

集成三道防线：
  防线一：只读用户连接（news_reader）
  防线二：sqlglot AST 审查（SQLAuditor）
  防线三：statement_timeout 会话级燔断
"""
from __future__ import annotations
import json
import os
import time
from typing import Any, Dict, List, Optional

try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_OK = True
except ImportError:
    PSYCOPG2_OK = False

from agentic_rag.db.security import PGSecurityConfig, SQLAuditor, DEFAULT_CONFIG


class SafePGExecutor:
    """
    安全 PostgreSQL 执行器。
    每次 query() 调用都经历：
      1. SQLAuditor.audit()  — AST 审查 + LIMIT 注入
      2. 建立短生命周期连接，设置 statement_timeout
      3. 执行只读查询，返回结果
    """

    def __init__(self, config: PGSecurityConfig = DEFAULT_CONFIG):
        self.cfg = config
        self.auditor = SQLAuditor(config)

    # ---------------------------------------------------------------- #
    #  连接工厂（每次查询新建连接，确保超时隔离）                         #
    # ---------------------------------------------------------------- #
    def _connect(self):
        if not PSYCOPG2_OK:
            raise RuntimeError("psycopg2 未安装，请执行: pip install psycopg2-binary")
        conn = psycopg2.connect(
            host=self.cfg.host,
            port=self.cfg.port,
            dbname=self.cfg.dbname,
            user=self.cfg.user,
            password=self.cfg.password,
            connect_timeout=10,
        )
        conn.set_session(readonly=True, autocommit=True)
        return conn

    def _connect_write(self):
        """Build a writable connection (non-readonly) for DDL and write operations."""
        if not PSYCOPG2_OK:
            raise RuntimeError("psycopg2 未安装，请执行: pip install psycopg2-binary")
        conn = psycopg2.connect(
            host=self.cfg.host,
            port=self.cfg.port,
            dbname=self.cfg.dbname,
            user=os.getenv("PG_WRITE_USER", self.cfg.user),
            password=os.getenv("PG_WRITE_PASSWORD", self.cfg.password),
            connect_timeout=10,
        )
        conn.set_session(readonly=False, autocommit=False)
        return conn

    # ---------------------------------------------------------------- #
    #  主查询接口                                                        #
    # ---------------------------------------------------------------- #
    def query(self, sql: str, params: Optional[tuple | list] = None) -> Dict[str, Any]:
        """
        执行一条 SQL，支持可选参数化查询。

        params 为元组/列表时，SQL 中的 %s 占位符会被安全替换（绕过 AST 审查，
        因为参数不参与 SQL 语法解析，天然免疫 SQL 注入）。

        返回：
          {
            "ok": bool,
            "sql_original": str,
            "sql_executed": str,        # 经审查/修改后实际执行的 SQL
            "rows": list[dict],
            "row_count": int,
            "columns": list[str],
            "elapsed_ms": float,
            "warnings": list[str],
            "error": str | None,
            "violations": list[str],
          }
        """
        result: Dict[str, Any] = {
            "ok": False,
            "sql_original": sql,
            "sql_executed": "",
            "rows": [],
            "row_count": 0,
            "columns": [],
            "elapsed_ms": 0.0,
            "warnings": [],
            "error": None,
            "violations": [],
        }

        # ---- 防线二：AST 审查（有 params 时跳过，因为参数不参与语法） --- #
        if params is None:
            audit = self.auditor.audit(sql)
            result["warnings"] = audit.warnings
            result["violations"] = audit.violations

            if not audit.allowed:
                result["error"] = "SQL 安全审查未通过: " + "; ".join(audit.violations)
                return result

            safe_sql = audit.sql
        else:
            safe_sql = sql
        result["sql_executed"] = safe_sql

        # ---- 防线一 + 防线三：只读连接 + 超时 ----------------------- #
        conn = None
        t0 = time.perf_counter()
        try:
            conn = self._connect()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            # 防线三：statement_timeout（毫秒）
            cur.execute(f"SET statement_timeout = '{self.cfg.statement_timeout_ms}'")
            cur.execute(safe_sql, params or ())

            rows = cur.fetchall()
            elapsed = (time.perf_counter() - t0) * 1000

            result["ok"] = True
            result["rows"] = [dict(r) for r in rows]
            result["row_count"] = len(rows)
            result["columns"] = [desc[0] for desc in cur.description] if cur.description else []
            result["elapsed_ms"] = round(elapsed, 2)

        except psycopg2.errors.QueryCanceled:
            result["error"] = (
                f"查询超时（超过 {self.cfg.statement_timeout_ms}ms）。"
                "请简化查询，加更多过滤条件或缩小时间范围。"
            )
        except psycopg2.OperationalError as e:
            result["error"] = f"数据库连接失败: {e}"
        except Exception as e:
            result["error"] = f"查询执行错误: {type(e).__name__}: {e}"
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)

        return result

    # ---------------------------------------------------------------- #
    #  写操作接口（DDL / INSERT / UPSERT，绕过只读限制）                  #
    # ---------------------------------------------------------------- #
    def execute(self, sql: str) -> Dict[str, Any]:
        """
        Execute write SQL (DDL / INSERT / UPDATE / UPSERT).
        Returns a result dict compatible with query().
        """
        result: Dict[str, Any] = {
            "ok": False,
            "sql_original": sql,
            "sql_executed": sql,
            "rows": [],
            "row_count": 0,
            "columns": [],
            "elapsed_ms": 0.0,
            "warnings": [],
            "error": None,
            "violations": [],
        }
        conn = None
        t0 = time.perf_counter()
        try:
            conn = self._connect_write()
            cur = conn.cursor()
            cur.execute(sql)
            conn.commit()
            result["ok"] = True
            result["row_count"] = cur.rowcount if cur.rowcount is not None else 0
        except psycopg2.errors.QueryCanceled:
            if conn:
                conn.rollback()
            result["error"] = f"写操作超时"
        except psycopg2.OperationalError as e:
            if conn:
                conn.rollback()
            result["error"] = f"数据库连接失败: {e}"
        except Exception as e:
            if conn:
                conn.rollback()
            result["error"] = f"写操作执行错误: {type(e).__name__}: {e}"
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            result["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return result


    def execute_returning(self, sql: str):
        """
        Execute INSERT/UPDATE ... RETURNING and return fetched rows.
        Same dict shape as query() but uses the write connection.
        """
        result = {"ok": False, "rows": [], "row_count": 0, "columns": [],
                  "error": None, "elapsed_ms": 0.0}
        conn = None
        import time as _time
        t0 = _time.perf_counter()
        try:
            conn = self._connect_write()
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            conn.commit()
            result["ok"] = True
            result["rows"] = [dict(zip([d[0] for d in cur.description], row)) for row in rows]
            result["row_count"] = len(rows)
            result["columns"] = [d[0] for d in cur.description] if cur.description else []
        except Exception as e:
            if conn:
                conn.rollback()
            result["error"] = f"{type(e).__name__}: {e}"
        finally:
            if conn:
                try: conn.close()
                except: pass
            result["elapsed_ms"] = round((_time.perf_counter() - t0) * 1000, 2)
        return result

    def get_write_conn(self):
        """Return a persistent write connection for batch operations."""
        return self._connect_write()

    # ---------------------------------------------------------------- #
    #  便捷工具                                                          #
    # ---------------------------------------------------------------- #
    def list_tables(self) -> Dict[str, Any]:
        """列出当前数据库所有用户表。"""
        sql = """
            SELECT table_schema, table_name,
                   pg_size_pretty(pg_total_relation_size(
                       quote_ident(table_schema)||'.'||quote_ident(table_name)
                   )) AS total_size
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY table_schema, table_name
            LIMIT 200
        """
        return self.query(sql)

    def describe_table(self, table_name: str) -> Dict[str, Any]:
        """查看表结构（列名、类型、是否可空）。"""
        # 表名用参数化白名单，防止注入
        safe_name = re.sub(r"[^a-zA-Z0-9_.]", "", table_name)
        sql = f"""
            SELECT column_name, data_type, is_nullable,
                   character_maximum_length, column_default
            FROM information_schema.columns
            WHERE table_name = '{safe_name}'
            ORDER BY ordinal_position
            LIMIT 100
        """
        return self.query(sql)

    def test_connection(self) -> Dict[str, Any]:
        """测试连接是否正常。"""
        return self.query("SELECT current_user, current_database(), version()")


# 模块级 re 导入（describe_table 用到）
import re

# ------------------------------------------------------------------ #
#  模块级单例（供 tools.py 调用）                                       #
# ------------------------------------------------------------------ #
_executor: Optional[SafePGExecutor] = None


def get_executor(config: PGSecurityConfig | None = None) -> SafePGExecutor:
    global _executor
    if _executor is None or config is not None:
        _executor = SafePGExecutor(config or DEFAULT_CONFIG)
    return _executor
