-- Valuation metrics mart: fuses price x fundamentals (§3.3).
-- One row per (ticker, freq, period_end). For each reported fiscal period we take
-- the close price on the nearest trading day at/just before period_end and combine
-- it with that period's fundamentals to derive P/E, P/B, market cap, etc. Some
-- ratios are null where the underlying line item is missing for AAPL — that's fine.
with fin as (
    select *
    from {{ ref('int_financials_pivoted') }}
),

prices as (
    select ticker, date, close, adj_close
    from {{ ref('eod_prices') }}
),

-- close price as of (nearest trading day at/before) each period_end
price_at_period as (
    select
        f.ticker,
        f.freq,
        f.period_end,
        p.close      as period_close,
        p.date       as price_date
    from fin f
    left join lateral (
        select pr.close, pr.date
        from prices pr
        where pr.ticker = f.ticker
          and pr.date <= f.period_end
        order by pr.date desc
        limit 1
    ) p on true
),

joined as (
    select
        f.ticker,
        f.freq,
        f.period_end,
        f.currency,
        pap.price_date,
        pap.period_close,
        f.total_revenue,
        f.gross_profit,
        f.operating_income,
        f.net_income,
        f.diluted_eps,
        f.basic_eps,
        f.total_assets,
        f.stockholders_equity,
        f.total_debt,
        f.cash_and_equivalents,
        -- prefer ordinary shares, fall back to shares issued / diluted avg shares
        coalesce(f.ordinary_shares_number, f.shares_issued, f.diluted_avg_shares) as shares_outstanding
    from fin f
    left join price_at_period pap
        on f.ticker = pap.ticker
       and f.freq = pap.freq
       and f.period_end = pap.period_end
)

select
    ticker,
    freq,
    period_end,
    currency,
    price_date,
    period_close,
    shares_outstanding,
    net_income,
    total_revenue,
    stockholders_equity,
    total_debt,
    cash_and_equivalents,
    diluted_eps,

    -- Market cap = close * shares outstanding
    case
        when period_close is not null and shares_outstanding is not null
            then round(period_close * shares_outstanding, 2)
    end as market_cap,

    -- P/E = price / diluted EPS  (for quarterly EPS this is a single-quarter P/E)
    case
        when period_close is not null and diluted_eps is not null and diluted_eps <> 0
            then round(period_close / diluted_eps, 4)
    end as pe_ratio,

    -- Book value per share = stockholders equity / shares
    case
        when stockholders_equity is not null and shares_outstanding is not null
             and shares_outstanding <> 0
            then round(stockholders_equity / shares_outstanding, 6)
    end as book_value_per_share,

    -- P/B = price / book value per share
    case
        when period_close is not null and stockholders_equity is not null
             and shares_outstanding is not null and shares_outstanding <> 0
             and stockholders_equity <> 0
            then round(period_close / (stockholders_equity / shares_outstanding), 4)
    end as pb_ratio,

    -- Net margin = net income / revenue
    case
        when net_income is not null and total_revenue is not null and total_revenue <> 0
            then round(net_income / total_revenue, 6)
    end as net_margin,

    -- ROE = net income / equity
    case
        when net_income is not null and stockholders_equity is not null
             and stockholders_equity <> 0
            then round(net_income / stockholders_equity, 6)
    end as return_on_equity,

    -- Enterprise value = market cap + total debt - cash
    case
        when period_close is not null and shares_outstanding is not null
            then round(
                period_close * shares_outstanding
                + coalesce(total_debt, 0)
                - coalesce(cash_and_equivalents, 0), 2)
    end as enterprise_value
from joined
