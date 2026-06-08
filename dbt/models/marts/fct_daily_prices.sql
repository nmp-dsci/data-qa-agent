-- Daily OHLCV + returns mart, built on int_prices_adjusted (§3.3).
-- Daily return uses split/dividend-adjusted close so it is not distorted by
-- corporate actions. Period returns (7d/30d) are trailing windows by row position.
with adj as (
    select *
    from {{ ref('int_prices_adjusted') }}
),

returns as (
    select
        ticker,
        date,
        open,
        high,
        low,
        close,
        adj_close,
        adj_open,
        adj_high,
        adj_low,
        volume,
        currency,
        dividend,
        split_ratio,
        lag(adj_close, 1)  over w as prev_adj_close,
        lag(adj_close, 7)  over w as adj_close_7d_ago,
        lag(adj_close, 30) over w as adj_close_30d_ago
    from adj
    window w as (partition by ticker order by date)
)

select
    ticker,
    date,
    open,
    high,
    low,
    close,
    adj_close,
    adj_open,
    adj_high,
    adj_low,
    volume,
    currency,
    dividend,
    split_ratio,
    prev_adj_close,
    case
        when prev_adj_close is not null and prev_adj_close <> 0
            then round((adj_close - prev_adj_close) / prev_adj_close, 6)
    end as daily_return,
    case
        when adj_close_7d_ago is not null and adj_close_7d_ago <> 0
            then round((adj_close - adj_close_7d_ago) / adj_close_7d_ago, 6)
    end as return_7d,
    case
        when adj_close_30d_ago is not null and adj_close_30d_ago <> 0
            then round((adj_close - adj_close_30d_ago) / adj_close_30d_ago, 6)
    end as return_30d
from returns
