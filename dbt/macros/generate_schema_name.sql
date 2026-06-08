{#
    Use custom schema names VERBATIM (no target-schema prefix).

    dbt's default behavior concatenates <target.schema>_<custom_schema>
    (e.g. public_fact_yfinance). The spec (§3.1) requires the resulting
    Postgres schemas to be exactly `fact_yfinance` and `analytics_yfinance`,
    so when a model/seed/snapshot declares a custom schema we emit it as-is.
    Models without a custom schema fall back to the profile's target schema.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set default_schema = target.schema -%}
    {%- if custom_schema_name is none -%}
        {{ default_schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
