-- data-qa-agent :: Row-Level Security
-- Isolation by default; the `admin` role sees across users (Decision A).
-- Context is set per request via  SELECT set_config('app.current_user_id', <uuid>, true).

-- marts.housing — visible only if the current user has access to the housing
-- dataset (or is admin). user2 has no grant, so it sees zero rows.
ALTER TABLE marts.housing ENABLE ROW LEVEL SECURITY;
CREATE POLICY housing_access ON marts.housing
    FOR SELECT
    USING (
        app.is_admin()
        OR EXISTS (
            SELECT 1
            FROM app.dataset_access da
            JOIN app.datasets d ON d.id = da.dataset_id
            WHERE da.user_id = app.current_user_id()
              AND d.slug = 'housing'
        )
    );

-- conversations — owner sees own; admin sees all.
ALTER TABLE app.conversations ENABLE ROW LEVEL SECURITY;
CREATE POLICY conversations_rw ON app.conversations
    FOR ALL
    USING (app.is_admin() OR user_id = app.current_user_id())
    WITH CHECK (user_id = app.current_user_id());

-- messages — owner sees own; admin sees all.
ALTER TABLE app.messages ENABLE ROW LEVEL SECURITY;
CREATE POLICY messages_rw ON app.messages
    FOR ALL
    USING (app.is_admin() OR user_id = app.current_user_id())
    WITH CHECK (user_id = app.current_user_id());

-- query_runs — every executed agent SQL statement is auditable by owner/admin.
ALTER TABLE app.query_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY query_runs_rw ON app.query_runs
    FOR ALL
    USING (app.is_admin() OR user_id = app.current_user_id())
    WITH CHECK (user_id = app.current_user_id());

-- user_memories — strictly owner-only (no admin override).
ALTER TABLE app.user_memories ENABLE ROW LEVEL SECURITY;
CREATE POLICY memories_rw ON app.user_memories
    FOR ALL
    USING (user_id = app.current_user_id())
    WITH CHECK (user_id = app.current_user_id());

-- events — anyone may log (incl. pre-login null user); admin reads all, users read own.
ALTER TABLE app.events ENABLE ROW LEVEL SECURITY;
CREATE POLICY events_insert ON app.events
    FOR INSERT
    WITH CHECK (true);
CREATE POLICY events_select ON app.events
    FOR SELECT
    USING (app.is_admin() OR user_id = app.current_user_id());
