{#-
  Use the model's +schema value verbatim (raw / staging / marts) instead of the
  default dbt behaviour of prefixing it with the target schema. Our schemas are
  created by the Alembic baseline and the agent queries `marts.*` by name.
-#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
