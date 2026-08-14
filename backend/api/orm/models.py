# models.py
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint, Boolean
from api.core.db import Base
from api.core.secrets import EncryptedSecretsText


class Language(Base):
    """语言表，通过 id 关联 news.language_id"""
    __tablename__ = "language"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255))


class News(Base):
    """流水线主表 public.news。"""
    __tablename__ = "news"

    id = Column(Integer, primary_key=True, index=True)
    request_url = Column("url", String(1000))
    title = Column(Text)
    abstract = Column(Text)
    body = Column(Text)
    pub_time = Column("published_at", DateTime)
    language_id = Column("language", String(10))

class V3Media(Base):
    """媒体源表（已爬取网站名称与域名）。"""
    __tablename__ = "v3_media"
    media_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255))
    domain = Column(String(255))


class LxyTranslated(Base):
    """翻译结果表"""
    __tablename__ = "lxy_translated"
    id = Column(Integer, primary_key=True, index=True)
    news_id = Column(Integer, index=True)
    website_id = Column(Integer)
    is_translated = Column(Boolean, default=False)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    trans_title = Column(String(500))
    trans_abstract = Column(Text)
    trans_body = Column(Text)

class User(Base):
    """应用用户表（物理表名 app_user；与 user_favorite / user_search_history 通过 user_id 关联）。"""
    __tablename__ = "app_user"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(128))
    email = Column(String(255), unique=True, index=True)
    phone = Column(String(32), unique=True, index=True)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    is_active = Column(Boolean, nullable=False, default=True)
    last_login_at = Column(DateTime)
    role = Column(String(32), nullable=False, default="user")
    avatar_url = Column(String(512))
    # Claude / LLM API 配置
    api_keys = Column(
        EncryptedSecretsText(),
        comment="加密保存的供应商 API Key JSON；由 USER_API_KEYS_ENCRYPTION_KEY 保护",
    )
    active_provider = Column(String(32), comment="当前使用供应商：deepseek / openai / anthropic / custom")
    default_model = Column(String(128), comment="默认模型名")
    base_url = Column(String(512), comment="自定义 API 端点地址")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_token"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("app_user.id"), index=True, nullable=False)
    token_hash = Column(String(64), unique=True, index=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False)


class UserSearchHistory(Base):
    __tablename__ = "user_search_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("app_user.id"), index=True, nullable=False)
    keyword = Column(String(255), nullable=False)
    created_at = Column(DateTime)


class UserFavorite(Base):
    __tablename__ = "user_favorite"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "news_id",
            "topic",
            "item_kind",
            name="uq_user_news_topic_kind",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("app_user.id"), index=True, nullable=False)
    news_id = Column(Integer, index=True, nullable=False)
    topic = Column(String(255), nullable=False, default="")
    item_kind = Column(String(16), nullable=False, default="favorite")
    created_at = Column(DateTime)


class AssistantChatSession(Base):
    """数据助手会话：按用户隔离。"""

    __tablename__ = "assistant_chat_session"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("app_user.id"), index=True, nullable=False)
    title = Column(String(256), nullable=False, default="新会话")
    pinned = Column(Boolean, default=False)
    context_summary = Column(Text)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class AssistantChatMessage(Base):
    """数据助手消息。"""

    __tablename__ = "assistant_chat_message"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(
        Integer,
        ForeignKey("assistant_chat_session.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id = Column(Integer, ForeignKey("app_user.id"), index=True, nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False, default="")
    extra_json = Column(Text)
    created_at = Column(DateTime)


class AssistantUserMemory(Base):
    """数据助手长期记忆：按用户隔离。"""

    __tablename__ = "assistant_user_memory"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("app_user.id"), unique=True, index=True, nullable=False)
    memory_summary = Column(Text, nullable=False, default="")
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
