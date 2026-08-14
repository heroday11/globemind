-- Owner-only migration required before switching Web to the web_runtime role.
-- Applied only by deploy/v093_database_schema.py inside its managed transaction.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $globemind_target_check$
BEGIN
    IF current_database() <> 'news'
       OR current_user <> 'postgres'
       OR session_user <> 'postgres'
       OR NOT coalesce(
           (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = current_user),
           false
       )
       OR NOT (
           (
               SELECT pg_catalog.pg_get_userbyid(nspowner)
               FROM pg_catalog.pg_namespace
               WHERE nspname = 'public'
           ) = 'postgres'
           OR (
               (
                   SELECT pg_catalog.pg_get_userbyid(nspowner)
                   FROM pg_catalog.pg_namespace
                   WHERE nspname = 'public'
               ) = 'pg_database_owner'
               AND (
                   SELECT pg_catalog.pg_get_userbyid(datdba)
                   FROM pg_catalog.pg_database
                   WHERE datname = current_database()
               ) = 'postgres'
           )
       ) THEN
        RAISE EXCEPTION 'fixed news/postgres/public-owner preflight failed';
    END IF;
END
$globemind_target_check$;

CREATE TABLE IF NOT EXISTS public.china_opinion_article_scores (
    news_id bigint PRIMARY KEY,
    published_at timestamptz,
    published_date date,
    language text,
    region text,
    media_source_id integer,
    media_domain text,
    source_domain text,
    event_family text,
    event_action text,
    initiator text,
    target text,
    location text,
    tone text,
    china_role text,
    directness text,
    directness_score double precision NOT NULL DEFAULT 0,
    stance_score double precision NOT NULL DEFAULT 0,
    confidence double precision NOT NULL DEFAULT 0,
    relevance_score double precision NOT NULL DEFAULT 0,
    article_weight double precision NOT NULL DEFAULT 0,
    target_scope text,
    evidence text,
    method_version text NOT NULL,
    scored_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_china_opinion_scores_date
    ON public.china_opinion_article_scores (published_date);
CREATE INDEX IF NOT EXISTS idx_china_opinion_scores_dims
    ON public.china_opinion_article_scores (region, language, media_domain, event_family);
CREATE INDEX IF NOT EXISTS idx_china_opinion_scores_direct
    ON public.china_opinion_article_scores (directness_score, relevance_score);

CREATE TABLE IF NOT EXISTS public.china_opinion_feedback (
    id bigserial PRIMARY KEY,
    news_id bigint NOT NULL,
    correction text NOT NULL CHECK (
        correction IN ('irrelevant', 'too_positive', 'too_negative', 'correct')
    ),
    page text,
    note text,
    current_impact_index double precision,
    sentiment double precision,
    created_at timestamptz NOT NULL DEFAULT now()
);

DO $globemind_feedback_constraint$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_constraint AS con
        WHERE con.conrelid = 'public.china_opinion_feedback'::regclass
          AND con.contype = 'c'
          AND pg_catalog.pg_get_constraintdef(con.oid) LIKE '%correction%'
          AND pg_catalog.pg_get_constraintdef(con.oid) LIKE '%irrelevant%'
          AND pg_catalog.pg_get_constraintdef(con.oid) LIKE '%too_positive%'
          AND pg_catalog.pg_get_constraintdef(con.oid) LIKE '%too_negative%'
          AND pg_catalog.pg_get_constraintdef(con.oid) LIKE '%correct%'
    ) THEN
        IF EXISTS (
            SELECT 1
            FROM pg_catalog.pg_constraint AS con
            WHERE con.conrelid = 'public.china_opinion_feedback'::regclass
              AND con.contype = 'c'
              AND pg_catalog.pg_get_constraintdef(con.oid) LIKE '%correction%'
        ) THEN
            RAISE EXCEPTION 'existing china_opinion_feedback correction CHECK is incompatible';
        END IF;
        ALTER TABLE public.china_opinion_feedback
            ADD CONSTRAINT china_opinion_feedback_correction_check
            CHECK (correction IN ('irrelevant', 'too_positive', 'too_negative', 'correct'));
    END IF;
END
$globemind_feedback_constraint$;

CREATE INDEX IF NOT EXISTS idx_china_opinion_feedback_news_created
    ON public.china_opinion_feedback (news_id, created_at DESC, id DESC);

DO $globemind_owner_check$
DECLARE
    object_name text;
    object_owner text;
BEGIN
    FOREACH object_name IN ARRAY ARRAY[
        'china_opinion_article_scores',
        'china_opinion_feedback'
    ] LOOP
        SELECT pg_get_userbyid(c.relowner)
          INTO object_owner
          FROM pg_catalog.pg_class AS c
          JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND c.relname = object_name;
        IF object_owner IS DISTINCT FROM 'postgres' THEN
            RAISE EXCEPTION 'unexpected owner for public.%', object_name;
        END IF;
    END LOOP;
END
$globemind_owner_check$;

DO $globemind_contract_check$
DECLARE
    feedback_sequence text;
BEGIN
    IF EXISTS (
        SELECT 1
        FROM (
            VALUES
                ('china_opinion_article_scores', 'news_id', 'int8', true, ''),
                ('china_opinion_article_scores', 'directness_score', 'float8', true, '0'),
                ('china_opinion_article_scores', 'stance_score', 'float8', true, '0'),
                ('china_opinion_article_scores', 'confidence', 'float8', true, '0'),
                ('china_opinion_article_scores', 'relevance_score', 'float8', true, '0'),
                ('china_opinion_article_scores', 'article_weight', 'float8', true, '0'),
                ('china_opinion_article_scores', 'method_version', 'text', true, ''),
                ('china_opinion_article_scores', 'scored_at', 'timestamptz', true, 'now()'),
                ('china_opinion_article_scores', 'updated_at', 'timestamptz', true, 'now()'),
                ('china_opinion_feedback', 'id', 'int8', true, 'nextval('),
                ('china_opinion_feedback', 'news_id', 'int8', true, ''),
                ('china_opinion_feedback', 'correction', 'text', true, ''),
                ('china_opinion_feedback', 'created_at', 'timestamptz', true, 'now()')
        ) AS expected(table_name, column_name, udt_name, must_not_null, default_marker)
        LEFT JOIN information_schema.columns AS actual
          ON actual.table_schema = 'public'
         AND actual.table_name = expected.table_name
         AND actual.column_name = expected.column_name
        WHERE actual.column_name IS NULL
           OR actual.udt_name <> expected.udt_name
           OR (expected.must_not_null AND actual.is_nullable <> 'NO')
           OR (
               expected.default_marker <> ''
               AND position(expected.default_marker IN lower(coalesce(actual.column_default, ''))) = 0
           )
    ) THEN
        RAISE EXCEPTION 'opinion column contract is incomplete or incompatible';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_constraint AS con
        WHERE con.conrelid = 'public.china_opinion_article_scores'::regclass
          AND con.contype = 'p'
          AND pg_catalog.pg_get_constraintdef(con.oid) = 'PRIMARY KEY (news_id)'
    ) THEN
        RAISE EXCEPTION 'china_opinion_article_scores requires PRIMARY KEY (news_id)';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_constraint AS con
        WHERE con.conrelid = 'public.china_opinion_feedback'::regclass
          AND con.contype = 'p'
          AND pg_catalog.pg_get_constraintdef(con.oid) = 'PRIMARY KEY (id)'
    ) THEN
        RAISE EXCEPTION 'china_opinion_feedback requires PRIMARY KEY (id)';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_constraint AS con
        WHERE con.conrelid = 'public.china_opinion_feedback'::regclass
          AND con.contype = 'c'
          AND pg_catalog.pg_get_constraintdef(con.oid) LIKE '%correction%'
          AND pg_catalog.pg_get_constraintdef(con.oid) LIKE '%irrelevant%'
          AND pg_catalog.pg_get_constraintdef(con.oid) LIKE '%too_positive%'
          AND pg_catalog.pg_get_constraintdef(con.oid) LIKE '%too_negative%'
          AND pg_catalog.pg_get_constraintdef(con.oid) LIKE '%correct%'
    ) THEN
        RAISE EXCEPTION 'china_opinion_feedback correction CHECK is incomplete';
    END IF;

    feedback_sequence := pg_catalog.pg_get_serial_sequence(
        'public.china_opinion_feedback', 'id'
    );
    IF feedback_sequence IS DISTINCT FROM 'public.china_opinion_feedback_id_seq' THEN
        RAISE EXCEPTION 'china_opinion_feedback.id sequence contract is incompatible';
    END IF;
    IF (
        SELECT pg_catalog.pg_get_userbyid(sequence.relowner)
        FROM pg_catalog.pg_class AS sequence
        WHERE sequence.oid = feedback_sequence::regclass
    ) IS DISTINCT FROM 'postgres' THEN
        RAISE EXCEPTION 'china_opinion_feedback sequence owner is incompatible';
    END IF;

    IF position('(published_date)' IN lower(pg_catalog.pg_get_indexdef(
        'public.idx_china_opinion_scores_date'::regclass
    ))) = 0 OR position(
        '(region, language, media_domain, event_family)'
        IN lower(pg_catalog.pg_get_indexdef('public.idx_china_opinion_scores_dims'::regclass))
    ) = 0 OR position(
        '(directness_score, relevance_score)'
        IN lower(pg_catalog.pg_get_indexdef('public.idx_china_opinion_scores_direct'::regclass))
    ) = 0 OR position(
        '(news_id, created_at DESC, id DESC)'
        IN pg_catalog.pg_get_indexdef('public.idx_china_opinion_feedback_news_created'::regclass)
    ) = 0 THEN
        RAISE EXCEPTION 'opinion index definitions are incompatible';
    END IF;
END
$globemind_contract_check$;
