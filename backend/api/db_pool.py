from api.core.environment import int_setting


def engine_pool_kwargs() -> dict:
    pool_size_fallback = int_setting("SQLALCHEMY_POOL_SIZE", 3)
    overflow_fallback = int_setting("SQLALCHEMY_MAX_OVERFLOW", 2)
    timeout_fallback = int_setting("SQLALCHEMY_POOL_TIMEOUT", 10)
    return {
        "pool_pre_ping": True,
        "pool_recycle": int_setting("DB_POOL_RECYCLE", 300),
        "pool_size": int_setting("DB_POOL_SIZE", pool_size_fallback),
        "max_overflow": int_setting("DB_MAX_OVERFLOW", overflow_fallback),
        "pool_timeout": int_setting("DB_POOL_TIMEOUT", timeout_fallback),
    }
