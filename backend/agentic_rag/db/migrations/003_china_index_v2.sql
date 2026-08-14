-- China Index v2 迁移：在现有 news_ai_analysis 表上增列多原型涉华评分
-- 执行：psql -h 192.168.207.171 -p 54333 -U postgres -d globemind_news -f 003_china_index_v2.sql

-- 1) 在 news_ai_analysis 上新增列（IF NOT EXISTS 确保可重复执行）
ALTER TABLE news_ai_analysis
    ADD COLUMN IF NOT EXISTS prototype_scores JSONB
        COMMENT '6维涉华原型分：{"中美战略竞争": 0.72, "中国外交与全球治理": 0.65, ...}',
    ADD COLUMN IF NOT EXISTS prototype_weighted DOUBLE PRECISION
        COMMENT '6维加权综合涉华指数 [0,1]',
    ADD COLUMN IF NOT EXISTS lexicon_score DOUBLE PRECISION
        COMMENT '层次化词典实体级涉华分数 [0,1]',
    ADD COLUMN IF NOT EXISTS lexicon_matches JSONB
        COMMENT '词典命中明细：{"核心实体": ["习近平", ...], "议题词": [...]}',
    ADD COLUMN IF NOT EXISTS china_index_version TEXT DEFAULT 'v2'
        COMMENT '涉华指数版本标记，从 v1（LLM单轮分类）升级为 v2（词典+多原型+可选LLM三级融合）';

-- 2) 为涉华指数创建索引（加速过滤）
CREATE INDEX IF NOT EXISTS idx_ai_china_index_weighted
    ON news_ai_analysis (prototype_weighted)
    WHERE prototype_weighted IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ai_china_index_version
    ON news_ai_analysis (china_index_version);

-- 3) 创建涉华指数时间序列表（全局聚合 + 每事件的月度轨迹）
CREATE TABLE IF NOT EXISTS china_index_timeseries (
    id          SERIAL PRIMARY KEY,
    event_id    INTEGER REFERENCES macro_event_coref(id) ON DELETE CASCADE,
    -- NULL event_id = 全局聚合行（非NULL = 特定事件）
    period      DATE NOT NULL,          -- 周期的起始日期（按月：2024-01-01）
    metric      TEXT NOT NULL,          -- attention / polarity / dispersion / event_mean
    value       DOUBLE PRECISION NOT NULL,
    sample_size INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (event_id, period, metric)
);

CREATE INDEX IF NOT EXISTS idx_china_ts_event
    ON china_index_timeseries (event_id, period);

CREATE INDEX IF NOT EXISTS idx_china_ts_metric
    ON china_index_timeseries (metric, period);

-- 4) 给现有数据打上 v1 版本标签
UPDATE news_ai_analysis
SET china_index_version = 'v1'
WHERE china_index_version IS NULL
  AND is_china_related IS NOT NULL
  AND china_relevance_score IS NOT NULL;
