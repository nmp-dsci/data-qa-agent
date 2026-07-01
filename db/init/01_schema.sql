-- data-qa-agent :: schema, roles, tables
-- Runs once on first container init (empty data volume).

CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector for agent memory
CREATE EXTENSION IF NOT EXISTS pgcrypto;    -- gen_random_uuid()

CREATE SCHEMA IF NOT EXISTS app;     -- application tables
CREATE SCHEMA IF NOT EXISTS raw;     -- pipeline landing zone (ingested CSVs)
CREATE SCHEMA IF NOT EXISTS staging; -- dbt staging layer (future)
CREATE SCHEMA IF NOT EXISTS marts;   -- clean, documented tables the agent queries

CREATE TABLE app.schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Roles.  Tables are owned by the superuser (postgres); these login roles are
-- non-owners and NOT superusers, so Row-Level Security applies to them.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
    CREATE ROLE app_user LOGIN PASSWORD 'app_pw' NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_ro') THEN
    CREATE ROLE agent_ro LOGIN PASSWORD 'agent_pw' NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Identity
-- ---------------------------------------------------------------------------
CREATE TABLE app.users (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entra_oid    text UNIQUE,                 -- external subject (prod); null in dev
    username     text UNIQUE NOT NULL,        -- dev-auth handle
    email        text UNIQUE NOT NULL,
    display_name text NOT NULL,
    role         text NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Datasets registry + access control
-- ---------------------------------------------------------------------------
CREATE TABLE app.datasets (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        text UNIQUE NOT NULL,
    name        text NOT NULL,
    description text,
    status      text NOT NULL DEFAULT 'ready',
    row_count   integer NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.dataset_access (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id uuid NOT NULL REFERENCES app.datasets(id) ON DELETE CASCADE,
    user_id    uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    access     text NOT NULL DEFAULT 'read',
    UNIQUE (dataset_id, user_id)
);

-- ---------------------------------------------------------------------------
-- Q&A history
-- ---------------------------------------------------------------------------
CREATE TABLE app.conversations (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    dataset_id uuid REFERENCES app.datasets(id) ON DELETE SET NULL,
    title      text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.messages (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES app.conversations(id) ON DELETE CASCADE,
    user_id         uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    role            text NOT NULL CHECK (role IN ('user', 'assistant')),
    content         text NOT NULL,
    sql_generated   text,
    tokens          integer,
    latency_ms      integer,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.query_runs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid REFERENCES app.conversations(id) ON DELETE SET NULL,
    message_id      uuid REFERENCES app.messages(id) ON DELETE SET NULL,
    user_id         uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    dataset_id      uuid REFERENCES app.datasets(id) ON DELETE SET NULL,
    question        text NOT NULL,
    sql_text        text,
    engine          text NOT NULL DEFAULT 'stub',
    row_count       integer NOT NULL DEFAULT 0,
    latency_ms      integer,
    status          text NOT NULL DEFAULT 'success' CHECK (status IN ('success', 'error')),
    error           text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON app.query_runs (user_id, created_at DESC);
CREATE INDEX ON app.query_runs (dataset_id);

-- ---------------------------------------------------------------------------
-- Agent memory (per-user preferences, pgvector-ready)
-- ---------------------------------------------------------------------------
CREATE TABLE app.user_memories (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    kind         text NOT NULL DEFAULT 'preference',
    content      text NOT NULL,
    embedding    vector(384),
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz
);

-- ---------------------------------------------------------------------------
-- Product analytics / event tracking
-- ---------------------------------------------------------------------------
CREATE TABLE app.events (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid REFERENCES app.users(id) ON DELETE SET NULL,  -- null = pre-login
    session_id text,
    event_type text NOT NULL,
    payload    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Domain data (pipeline output the agent answers over)
-- ---------------------------------------------------------------------------
CREATE TABLE raw.housing (
    id            integer,
    suburb        text,
    property_type text,
    price         integer,
    bedrooms      integer,
    bathrooms     integer,
    car_spaces    integer,
    land_size_sqm integer,
    year_built    integer,
    sale_date     date
);

CREATE TABLE marts.housing (
    id            integer,
    dataset_id    uuid NOT NULL REFERENCES app.datasets(id) ON DELETE CASCADE,
    suburb        text NOT NULL,
    property_type text NOT NULL,
    price         integer NOT NULL,
    bedrooms      integer NOT NULL,
    bathrooms     integer NOT NULL,
    car_spaces    integer NOT NULL,
    land_size_sqm integer NOT NULL,
    year_built    integer NOT NULL,
    sale_date     date NOT NULL
);
CREATE INDEX ON marts.housing (suburb);
CREATE INDEX ON marts.housing (dataset_id);

-- ---------------------------------------------------------------------------
-- Helper functions used by RLS policies
-- ---------------------------------------------------------------------------
CREATE FUNCTION app.current_user_id() RETURNS uuid
    LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('app.current_user_id', true), '')::uuid
$$;

CREATE FUNCTION app.is_admin() RETURNS boolean
    LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM app.users u
        WHERE u.id = app.current_user_id() AND u.role = 'admin'
    )
$$;

-- ---------------------------------------------------------------------------
-- Privileges
-- ---------------------------------------------------------------------------
GRANT USAGE ON SCHEMA app, marts TO app_user, agent_ro;

-- backend (app_user): read/write app tables, read marts
GRANT SELECT, INSERT, UPDATE, DELETE
    ON app.users, app.datasets, app.dataset_access, app.conversations,
       app.messages, app.query_runs, app.user_memories, app.events
    TO app_user;
GRANT SELECT, INSERT ON app.schema_migrations TO app_user;
GRANT SELECT ON marts.housing TO app_user;

-- agent (agent_ro): strictly read-only, only what it needs to reason + query
GRANT SELECT ON marts.housing, app.datasets, app.dataset_access, app.users TO agent_ro;

GRANT EXECUTE ON FUNCTION app.current_user_id(), app.is_admin() TO app_user, agent_ro;
