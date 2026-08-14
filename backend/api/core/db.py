# database.py
import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from api.core.database_credentials import canonical_database_settings, canonical_postgresql_url
from api.core.environment import is_test_environment, string_setting
from api.core.identity_security import hash_password
from api.db_pool import engine_pool_kwargs

# PostgreSQL：与 agentic_rag 流水线对齐（默认库 news，表 news）
# 可用 DB_NAME 覆盖；未设置时优先 PG_DATABASE / PG_DBNAME，最后 news
_DATABASE_SETTINGS = canonical_database_settings()
DB_HOST = _DATABASE_SETTINGS["host"]
DB_PORT = _DATABASE_SETTINGS["port"]
DB_USER = _DATABASE_SETTINGS["user"]
DB_NAME = _DATABASE_SETTINGS["database"]

SQLALCHEMY_DATABASE_URL = canonical_postgresql_url()

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    **engine_pool_kwargs(),
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _dispose_inherited_engine_pool() -> None:
    """Replace an inherited pool in forked children without closing parent connections."""
    engine.dispose(close=False)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_dispose_inherited_engine_pool)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_tables():
    if is_test_environment():
        raise RuntimeError("Schema mutation is disabled in tests")
    # 延迟导入以避免循环依赖
    from api.orm import models as _models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    # 兼容已有库：在不破坏现有表的前提下补齐新增字段/约束
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE assistant_chat_session ADD COLUMN IF NOT EXISTS pinned BOOLEAN DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE assistant_chat_session ADD COLUMN IF NOT EXISTS context_summary TEXT"))
        conn.execute(text("ALTER TABLE public.app_user ADD COLUMN IF NOT EXISTS full_name VARCHAR(128)"))
        conn.execute(text("ALTER TABLE public.app_user ADD COLUMN IF NOT EXISTS email VARCHAR(255)"))
        conn.execute(text("ALTER TABLE public.app_user ADD COLUMN IF NOT EXISTS phone VARCHAR(32)"))
        conn.execute(
            text(
                "ALTER TABLE public.app_user ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE public.app_user ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE public.app_user ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE public.app_user ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE public.app_user ADD COLUMN IF NOT EXISTS role VARCHAR(32) DEFAULT 'user'"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE public.app_user ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(512)"
            )
        )
        conn.execute(text("ALTER TABLE public.app_user ADD COLUMN IF NOT EXISTS api_keys TEXT"))
        conn.execute(
            text("ALTER TABLE public.app_user ADD COLUMN IF NOT EXISTS active_provider VARCHAR(32)")
        )
        conn.execute(
            text("ALTER TABLE public.app_user ADD COLUMN IF NOT EXISTS default_model VARCHAR(128)")
        )
        conn.execute(
            text("ALTER TABLE public.app_user ADD COLUMN IF NOT EXISTS base_url VARCHAR(512)")
        )
        conn.execute(
            text(
                "ALTER TABLE public.user_favorite ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE public.user_favorite ADD COLUMN IF NOT EXISTS topic VARCHAR(255) NOT NULL DEFAULT ''"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE public.user_favorite ADD COLUMN IF NOT EXISTS item_kind VARCHAR(16) NOT NULL DEFAULT 'favorite'"
            )
        )
        conn.execute(text("ALTER TABLE public.user_favorite DROP CONSTRAINT IF EXISTS uq_user_news_favorite"))
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_news_topic_kind "
                "ON public.user_favorite (user_id, news_id, topic, item_kind)"
            )
        )
        # 与前端默认主题名对齐，避免旧数据 topic='' 与新主题名不一致导致不同步
        conn.execute(
            text(
                "UPDATE public.user_favorite SET topic = '新闻分析主题' "
                "WHERE topic IS NULL OR topic = ''"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE public.user_search_history ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ"
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_app_user_email ON public.app_user (email) "
                "WHERE email IS NOT NULL AND email <> ''"
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_app_user_phone ON public.app_user (phone) "
                "WHERE phone IS NOT NULL AND phone <> ''"
            )
        )

    ensure_default_app_user()

def ensure_default_app_user() -> None:
    """
    若 `public.app_user` 中不存在 id=1，则插入默认管理员行并同步序列。
    避免 JWT 或前端仍使用 user_id=1 时写入 user_search_history / user_favorite 触发外键错误。

    password_hash 使用 bcrypt 存储。仅通过显式种子变量配置账号口令：
      SEED_DEFAULT_USERNAME（默认 ADMIN_USER / admin）
      SEED_DEFAULT_USER_PASSWORD（未配置则不创建）
    """
    auth_user = string_setting("ADMIN_USER", "admin")
    seed_name = string_setting("SEED_DEFAULT_USERNAME", auth_user)
    seed_pw = string_setting("SEED_DEFAULT_USER_PASSWORD")
    if not seed_pw:
        print("[seed] 未配置 SEED_DEFAULT_USER_PASSWORD，跳过默认用户创建", flush=True)
        return

    try:
        with engine.begin() as conn:
            exists = conn.execute(text("SELECT 1 FROM public.app_user WHERE id = 1 LIMIT 1")).scalar()
            if exists:
                return
            taken = conn.execute(
                text("SELECT 1 FROM public.app_user WHERE username = :u LIMIT 1"),
                {"u": seed_name},
            ).scalar()
            username = seed_name if not taken else "__system_uid_1__"
            now = datetime.now(timezone.utc)
            conn.execute(
                text(
                    """
                    INSERT INTO public.app_user (
                        id, username, password_hash, full_name, email, phone,
                        created_at, updated_at, is_active, role
                    ) VALUES (
                        1, :username, :password_hash, :full_name, NULL, NULL,
                        :now, :now, TRUE, 'admin'
                    )
                    """
                ),
                {
                    "username": username,
                    "password_hash": hash_password(seed_pw),
                    "full_name": "Default Admin",
                    "now": now,
                },
            )
            try:
                conn.execute(
                    text(
                        "SELECT setval("
                        "pg_get_serial_sequence('public.app_user', 'id'), "
                        "GREATEST((SELECT COALESCE(MAX(id), 1) FROM public.app_user), 1)"
                        ")"
                    )
                )
            except Exception:
                pass
        print(
            f"[seed] 已创建默认 app_user id=1（username={username}）。"
            "请尽快修改一次性种子口令。",
            flush=True,
        )
    except Exception as e:
        print(f"[seed] 默认用户种子失败（可忽略若表不存在）: {e}", flush=True)
