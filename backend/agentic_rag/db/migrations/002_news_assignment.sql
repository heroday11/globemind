-- 手工执行：与 ensure_news_assignment_table + 迁移脚本等价（执行前请备份）。

CREATE TABLE IF NOT EXISTS news_assignment (
    news_id BIGINT PRIMARY KEY REFERENCES news(id) ON DELETE CASCADE,
    entity_hash TEXT,
    micro_event_id BIGINT,
    assign_score DOUBLE PRECISION,
    assigned_at TIMESTAMPTZ,
    embedding_version TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_news_assignment_micro_event
ON news_assignment (micro_event_id)
WHERE micro_event_id IS NOT NULL;

-- 以下为示例：若列仍在 news 上，按实际存在的列调整 INSERT 后执行 DROP。
-- INSERT INTO news_assignment (news_id, entity_hash, micro_event_id, assign_score, assigned_at, embedding_version, updated_at)
-- SELECT n.id, n.entity_hash, n.micro_event_id, n.assign_score, n.assigned_at, n.embedding_version, now()
-- FROM news n
-- WHERE ... ;
-- ALTER TABLE news DROP COLUMN IF EXISTS entity_hash;
-- ALTER TABLE news DROP COLUMN IF EXISTS micro_event_id;
-- ALTER TABLE news DROP COLUMN IF EXISTS assign_score;
-- ALTER TABLE news DROP COLUMN IF EXISTS assigned_at;
-- ALTER TABLE news DROP COLUMN IF EXISTS embedding_version;
