{#-
  Post-hook applied to each mart: dbt recreates the table on every run (owned by
  the admin role, so RLS applies to the non-owner app/agent roles), so we must
  re-enable RLS, (re)create the access policy, and re-grant SELECT each time.
  The policy scopes rows to users granted ALL of the given dataset(s) (admins see
  all) — the same isolation model as the hand-written policies in the Alembic
  baseline. Accepts a single slug or a list; a mart derived from multiple
  datasets requires the user to hold every listed grant.

  PERF: the whole predicate is wrapped in a scalar `(select ...)` so Postgres
  evaluates it ONCE per query (an InitPlan) instead of per row. A bare
  `app.is_admin()` in an RLS filter is re-invoked for every scanned row — even
  though the function is STABLE — turning a 50ms mart scan into ~2.5s over 800k
  rows (and Explore fires ~20 such scans at once). Keep the outer `(select ...)`.
-#}
{% macro apply_dataset_rls(dataset_slugs) %}
{% if dataset_slugs is string %}{% set dataset_slugs = [dataset_slugs] %}{% endif %}
alter table {{ this }} enable row level security;
drop policy if exists rls_{{ this.identifier }} on {{ this }};
create policy rls_{{ this.identifier }} on {{ this }}
    for select using (
        (select
            app.is_admin()
            or (
                {%- for slug in dataset_slugs %}
                exists (
                    select 1
                    from app.dataset_access da
                    join app.datasets d on d.id = da.dataset_id
                    where da.user_id = app.current_user_id()
                      and d.slug = '{{ slug }}'
                )
                {%- if not loop.last %} and {% endif -%}
                {% endfor %}
            )
        )
    );
grant select on {{ this }} to app_user, agent_ro;
{% endmacro %}

{#-
  ANY-of variant: a shared dimension readable by a user holding at least ONE of
  the listed dataset grants (admins see all). dim_postcode_geo is the geo
  resolver for every property dataset, so a user granted only nsw_rent must
  still be able to roll rent up to SA3/SA4 — but holding no property grant at
  all still sees nothing. This is the OR complement to apply_dataset_rls's AND.
-#}
{% macro apply_dataset_rls_any(dataset_slugs) %}
{% if dataset_slugs is string %}{% set dataset_slugs = [dataset_slugs] %}{% endif %}
alter table {{ this }} enable row level security;
drop policy if exists rls_{{ this.identifier }} on {{ this }};
create policy rls_{{ this.identifier }} on {{ this }}
    for select using (
        {#- wrapped in a scalar (select ...) so is_admin() is evaluated once per
            query, not once per row — see apply_dataset_rls for the full rationale -#}
        (select
            app.is_admin()
            or exists (
                select 1
                from app.dataset_access da
                join app.datasets d on d.id = da.dataset_id
                where da.user_id = app.current_user_id()
                  and d.slug in (
                      {%- for slug in dataset_slugs -%}
                      '{{ slug }}'{% if not loop.last %}, {% endif %}
                      {%- endfor -%}
                  )
            )
        )
    );
grant select on {{ this }} to app_user, agent_ro;
{% endmacro %}
