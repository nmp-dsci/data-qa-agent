{#-
  Post-hook applied to each mart: dbt recreates the table on every run (owned by
  the admin role, so RLS applies to the non-owner app/agent roles), so we must
  re-enable RLS, (re)create the access policy, and re-grant SELECT each time.
  The policy scopes rows to users granted the given dataset (admins see all) —
  the same isolation model as the hand-written policies in the Alembic baseline.
-#}
{% macro apply_dataset_rls(dataset_slug) %}
alter table {{ this }} enable row level security;
drop policy if exists rls_{{ this.identifier }} on {{ this }};
create policy rls_{{ this.identifier }} on {{ this }}
    for select using (
        app.is_admin()
        or exists (
            select 1
            from app.dataset_access da
            join app.datasets d on d.id = da.dataset_id
            where da.user_id = app.current_user_id()
              and d.slug = '{{ dataset_slug }}'
        )
    );
grant select on {{ this }} to app_user, agent_ro;
{% endmacro %}
