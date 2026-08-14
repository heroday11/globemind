"""
PostgreSQL 安全查询层 — 三道防线

防线一：只读数据库用户（连接层）
  - 使用 news_reader 账号，PostgreSQL 级别只有 SELECT 权限

防线二：sqlglot AST 语法树审查（代码层）
  - 拒绝非 SELECT 语句（INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE）
  - 检查黑名单表
  - 强制注入 LIMIT（防止全表扫描）
  - 深度检查子查询

防线三：statement_timeout 熔断（数据库会话层）
  - 每个查询会话强制 5 秒超时
  - 防止笛卡尔积/低效查询打爆 CPU
"""
from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

from agentic_rag.db_runtime_config import require_database_password

try:
    import sqlglot
    import sqlglot.expressions as exp
    SQLGLOT_OK = True
except ImportError:
    SQLGLOT_OK = False


# ------------------------------------------------------------------ #
#  配置                                                                #
# ------------------------------------------------------------------ #
@dataclass
class PGSecurityConfig:
    # 数据库连接
    host: str = "127.0.0.1"
    port: int = 5432
    dbname: str = "postgres"   # locked: only connect to postgres db
    user: str = os.getenv("PG_USER", "news_reader")
    password: str = field(
        default_factory=lambda: require_database_password("PG_PASSWORD", "DB_PASSWORD")
    )

    # 防线三：超时（毫秒）
    statement_timeout_ms: int = 5000

    # 防线二：强制 LIMIT
    max_rows: int = 500
    force_limit: bool = True

    # 防线二：黑名单表（敏感表，禁止 AI 访问）
    blocked_tables: List[str] = field(default_factory=lambda: [
        "pg_shadow", "pg_authid", "pg_user",
        "admin_users", "users", "credentials",
        "passwords", "secrets", "api_keys",
    ])

    # 防线二：允许的顶层语句类型（白名单）
    allowed_statement_types: List[str] = field(default_factory=lambda: ["SELECT"])


DEFAULT_CONFIG = PGSecurityConfig()


# ------------------------------------------------------------------ #
#  防线二：AST 安全审查器                                              #
# ------------------------------------------------------------------ #
@dataclass
class AuditResult:
    allowed: bool
    sql: str                          # 审查后（可能已修改）的 SQL
    violations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class SQLAuditor:
    """
    使用 sqlglot 解析 SQL AST，执行三类检查：
    1. 语句类型白名单（只允许 SELECT）
    2. 黑名单表访问检查
    3. 强制 LIMIT 注入
    """

    def __init__(self, config: PGSecurityConfig = DEFAULT_CONFIG):
        self.cfg = config

    # ---- 主入口 ---------------------------------------------------- #
    def audit(self, sql: str) -> AuditResult:
        sql = sql.strip().rstrip(";")  # 去掉末尾分号避免解析歧义

        if not SQLGLOT_OK:
            # sqlglot 未安装时降级为基础正则检查
            return self._fallback_audit(sql)

        try:
            statements = sqlglot.parse(sql, dialect="postgres")
        except Exception as e:
            return AuditResult(
                allowed=False,
                sql=sql,
                violations=[f"SQL 解析失败: {e}"]
            )

        if not statements:
            return AuditResult(allowed=False, sql=sql,
                               violations=["空 SQL 语句"])

        violations: List[str] = []
        warnings: List[str] = []

        for stmt in statements:
            # 检查1：语句类型白名单
            stmt_type = type(stmt).__name__.upper()
            if stmt_type not in [s.upper() for s in self.cfg.allowed_statement_types]:
                violations.append(
                    f"禁止的语句类型: {stmt_type}。只允许 SELECT。"
                )

            # 检查2：黑名单表
            for table in stmt.find_all(exp.Table):
                tname = table.name.lower() if table.name else ""
                if tname in [b.lower() for b in self.cfg.blocked_tables]:
                    violations.append(f"禁止访问敏感表: '{tname}'")

            # 检查3：危险函数（pg_read_file, lo_export 等）
            for func in stmt.find_all(exp.Anonymous):
                fname = (func.name or "").lower()
                if fname in ("pg_read_file", "pg_ls_dir", "lo_export",
                             "copy", "pg_read_binary_file"):
                    violations.append(f"禁止的危险函数: {fname}")

        if violations:
            return AuditResult(allowed=False, sql=sql, violations=violations)

        # 检查4：强制 LIMIT（修改 AST）
        safe_sql = sql
        if self.cfg.force_limit:
            safe_sql, w = self._inject_limit(sql)
            warnings.extend(w)

        return AuditResult(allowed=True, sql=safe_sql, warnings=warnings)

    # ---- LIMIT 注入 ------------------------------------------------- #

    def _inject_limit(self, sql: str) -> tuple[str, list]:
        """Force LIMIT <= max_rows. Uses sqlglot 30.x API (value in .args['expression'])."""
        warnings: list = []
        try:
            tree = sqlglot.parse_one(sql, dialect="postgres")
        except Exception:
            return sql, []

        if not isinstance(tree, exp.Select):
            return sql, []

        def _make_limit(n: int):
            tmpl = sqlglot.parse_one(f"SELECT 1 LIMIT {n}", dialect="postgres")
            return tmpl.args["limit"]

        existing = tree.args.get("limit")
        if existing:
            lim_expr = existing.args.get("expression")
            try:
                limit_val = int(lim_expr.this) if lim_expr is not None else self.cfg.max_rows + 1
            except Exception:
                limit_val = self.cfg.max_rows + 1
            if limit_val > self.cfg.max_rows:
                tree.set("limit", _make_limit(self.cfg.max_rows))
                warnings.append(f"LIMIT {limit_val} 超过上限，已自动压缩为 {self.cfg.max_rows}")
        else:
            tree.set("limit", _make_limit(self.cfg.max_rows))
            warnings.append(f"已自动追加 LIMIT {self.cfg.max_rows}")

        return tree.sql(dialect="postgres"), warnings

    # ---- 降级正则检查（sqlglot 未安装时）--------------------------- #
    def _fallback_audit(self, sql: str) -> AuditResult:
        upper = sql.upper().strip()
        forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
                     "TRUNCATE", "CREATE", "GRANT", "REVOKE", "COPY"]
        for kw in forbidden:
            if re.search(rf"\b{kw}\b", upper):
                return AuditResult(
                    allowed=False, sql=sql,
                    violations=[f"[降级检查] 检测到禁止关键词: {kw}"]
                )
        if not upper.startswith("SELECT"):
            return AuditResult(
                allowed=False, sql=sql,
                violations=["[降级检查] 只允许 SELECT 语句"]
            )
        # 追加 LIMIT
        if "LIMIT" not in upper:
            sql = sql + f" LIMIT {self.cfg.max_rows}"
        return AuditResult(allowed=True, sql=sql,
                           warnings=["sqlglot 未安装，使用正则降级检查"])
