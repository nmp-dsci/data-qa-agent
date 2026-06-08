{#
    SCD2 history of financial-statement restatements (§3.4).

    Strategy `check` on `value`, keyed by the natural key. The query selects the
    CURRENT (latest-ingested) value per natural key from the deduped fact layer;
    each dbt snapshot run compares it to the prior snapshot and, when `value`
    changes (a restatement), closes the old row (dbt_valid_to) and opens a new
    one — turning raw restatement history into clean point-in-time SCD2.
#}
{% snapshot financial_statements_snapshot %}
{{
    config(
        target_schema='fact_yfinance',
        unique_key='snapshot_key',
        strategy='check',
        check_cols=['value'],
        invalidate_hard_deletes=False
    )
}}

select
    ticker || '|' || statement || '|' || freq || '|'
        || period_end || '|' || line_item            as snapshot_key,
    ticker,
    statement,
    freq,
    period_end,
    line_item,
    value,
    currency,
    ingested_at
from {{ ref('financial_statements') }}

{% endsnapshot %}
