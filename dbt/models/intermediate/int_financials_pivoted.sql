-- Long -> wide financials, one row per (ticker, freq, period_end), on the deduped
-- fact_yfinance layer (§3.3). Pivots the line items needed by the valuation marts;
-- new line items can be added as additional max(case ...) columns without reshaping.
with fin as (
    select
        ticker,
        statement,
        freq,
        period_end,
        line_item,
        value,
        currency
    from {{ ref('financial_statements') }}
)

select
    ticker,
    freq,
    period_end,
    max(currency) as currency,

    -- Income statement
    max(case when statement = 'income_statement' and line_item = 'TotalRevenue'   then value end) as total_revenue,
    max(case when statement = 'income_statement' and line_item = 'GrossProfit'     then value end) as gross_profit,
    max(case when statement = 'income_statement' and line_item = 'OperatingIncome' then value end) as operating_income,
    max(case when statement = 'income_statement' and line_item = 'NetIncome'       then value end) as net_income,
    max(case when statement = 'income_statement' and line_item = 'DilutedEPS'      then value end) as diluted_eps,
    max(case when statement = 'income_statement' and line_item = 'BasicEPS'        then value end) as basic_eps,
    max(case when statement = 'income_statement' and line_item = 'DilutedAverageShares' then value end) as diluted_avg_shares,
    max(case when statement = 'income_statement' and line_item = 'BasicAverageShares'   then value end) as basic_avg_shares,

    -- Balance sheet
    max(case when statement = 'balance_sheet' and line_item = 'TotalAssets'         then value end) as total_assets,
    max(case when statement = 'balance_sheet' and line_item = 'StockholdersEquity'  then value end) as stockholders_equity,
    max(case when statement = 'balance_sheet' and line_item = 'TotalDebt'           then value end) as total_debt,
    max(case when statement = 'balance_sheet' and line_item = 'CashAndCashEquivalents' then value end) as cash_and_equivalents,
    max(case when statement = 'balance_sheet' and line_item = 'OrdinarySharesNumber' then value end) as ordinary_shares_number,
    max(case when statement = 'balance_sheet' and line_item = 'ShareIssued'         then value end) as shares_issued,

    -- Cash flow
    max(case when statement = 'cash_flow' and line_item = 'FreeCashFlow'        then value end) as free_cash_flow,
    max(case when statement = 'cash_flow' and line_item = 'OperatingCashFlow'   then value end) as operating_cash_flow
from fin
group by ticker, freq, period_end
