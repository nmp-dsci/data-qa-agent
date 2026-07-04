---
name: growth-rates
description: How to compute N-year growth rates off a 6-month rolling base, not raw months.
applies_to: [growth, "growth rate", cagr, "5 year", "10 year", "3 year", appreciation, increase]
---

# Growth rates

Never compute growth from two single raw months — monthly buckets are thin and
noisy, so a single-month endpoint can swing a growth figure by tens of percent.
Always compute growth off a **6-month rolling average** base: it smooths the
noise while staying responsive, and it's the same window the chart line uses so
the number and the picture agree ([[analysis/rolling-averages]]).

## Method (use the tools, do not do arithmetic in your head)
1. Build the monthly series per entity: `avg_price = total_sale_value / n_sold`
   (or `median_price` if the question asks specifically for the median). Keep
   every month — do not filter thin ones out ([[analysis/latest-month]]).
2. Compute a 6-month rolling average of that series (`rolling_average` tool,
   window=6) — see [[analysis/rolling-averages]].
3. Growth over N years:
   `(rolling_6mo[latest] - rolling_6mo[latest - N*12]) / rolling_6mo[latest - N*12] * 100`.
   Use the `growth_rate` tool with `years=N` — it does exactly this on the
   6-month base and returns a number you then state verbatim. **Never assert a
   growth figure the tool did not return.**
4. Anchor "latest" on the most recent month with data ([[analysis/latest-month]]);
   the 6-month rolling value at that month is what you report.

## Presenting growth
- Report 3 / 5 / 10-year windows when the series is long enough; skip a window if
  there isn't `N*12` months of history before the latest point.
- Alias the computed field `growth_pct` (or `sales_growth_pct` / `rent_growth_pct`)
  consistently so downstream consumers can rely on the name.
- A growth number is a headline value; the *insight* is the comparison or trend it
  reveals ([[presentation/what-makes-an-insight]]).
