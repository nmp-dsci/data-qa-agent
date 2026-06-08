-- Adjusted OHLC building block, on the deduped fact_yfinance layer (§3.3).
--
-- yfinance gives us raw OHLC + a back-adjusted `adj_close` (already reflecting
-- splits & dividends). The per-day adjustment factor implied by Yahoo is
-- adj_close / close; applying it to the raw OHLC yields a fully split/dividend
-- adjusted OHLC series that stays internally consistent with adj_close.
--
-- We also surface the split/dividend events from fact_yfinance.corporate_actions
-- so downstream consumers can audit WHY a given day was adjusted.
with prices as (
    select *
    from {{ ref('eod_prices') }}
),

actions as (
    select
        ticker,
        date,
        max(case when action_type = 'dividend' then value end) as dividend,
        max(case when action_type = 'split'    then value end) as split_ratio
    from {{ ref('corporate_actions') }}
    group by ticker, date
),

adjusted as (
    select
        p.ticker,
        p.date,
        p.open,
        p.high,
        p.low,
        p.close,
        p.adj_close,
        p.volume,
        p.currency,
        -- Yahoo's implied back-adjustment factor for this trading day.
        case
            when p.close is not null and p.close <> 0
                then p.adj_close / p.close
        end as adj_factor,
        a.dividend,
        a.split_ratio
    from prices p
    left join actions a
        on p.ticker = a.ticker
       and p.date = a.date
)

select
    ticker,
    date,
    open,
    high,
    low,
    close,
    adj_close,
    round(open  * adj_factor, 6) as adj_open,
    round(high  * adj_factor, 6) as adj_high,
    round(low   * adj_factor, 6) as adj_low,
    adj_factor,
    volume,
    currency,
    dividend,
    split_ratio
from adjusted
