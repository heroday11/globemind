-- 可选手工执行：与代码中 ensure_news_analysis_table + 自动迁移等价。
-- 执行前请备份数据库。

-- 1) 分析结果表（与 news 一对一）
CREATE TABLE IF NOT EXISTS news_analysis (
    news_id BIGINT PRIMARY KEY REFERENCES news(id) ON DELETE CASCADE,
    is_china_related BOOLEAN,
    china_related_index DOUBLE PRECISION,
    entities JSONB,
    sentiment_analysis TEXT,
    topic_classification TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_news_analysis_china
ON news_analysis (is_china_related)
WHERE is_china_related IS NOT NULL;

-- 2) 从旧 news 列拷贝（若仍存在）
--    若 entities 类型特殊导致失败，请用 Python/脚本逐行转 JSONB 后再 DROP。
INSERT INTO news_analysis (
    news_id, is_china_related, china_related_index, entities,
    sentiment_analysis, topic_classification, updated_at
)
SELECT
    n.id,
    n.is_china_related,
    n.china_related_index,
    CASE WHEN n.entities IS NULL THEN NULL ELSE n.entities::jsonb END,
    n.sentiment_analysis,
    n.topic_classification,
    now()
FROM news n
WHERE n.is_china_related IS NOT NULL
   OR n.sentiment_analysis IS NOT NULL
   OR n.topic_classification IS NOT NULL
   OR n.entities IS NOT NULL
   OR n.china_related_index IS NOT NULL
ON CONFLICT (news_id) DO UPDATE SET
    is_china_related = EXCLUDED.is_china_related,
    china_related_index = EXCLUDED.china_related_index,
    entities = EXCLUDED.entities,
    sentiment_analysis = EXCLUDED.sentiment_analysis,
    topic_classification = EXCLUDED.topic_classification,
    updated_at = now();

-- 3) 从 news 删除已迁移列（与 Python 迁移脚本一致；执行前请备份）
ALTER TABLE news DROP COLUMN IF EXISTS is_china_related;
ALTER TABLE news DROP COLUMN IF EXISTS china_related_index;
ALTER TABLE news DROP COLUMN IF EXISTS entities;
ALTER TABLE news DROP COLUMN IF EXISTS sentiment_analysis;
ALTER TABLE news DROP COLUMN IF EXISTS topic_classification;
