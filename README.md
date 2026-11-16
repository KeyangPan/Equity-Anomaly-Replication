# Equity-Anomaly-Replication

Replication of four published cross-sectional anomalies in US equities, 1999 to 2026:
how much of their return is still there after publication.

Work in progress. Transaction costs are not included yet.

## Signals

| Signal | Paper | Score at month-end t |
|---|---|---|
| Momentum | Jegadeesh and Titman (1993) | return from t-12 to t-1, skipping the latest month |
| Short-term reversal | Jegadeesh (1990) | minus the return of month t |
| Low volatility | Ang, Hodrick, Xing and Zhang (2006) | minus the std of daily Fama-French three-factor residuals in month t |
| Post-earnings drift | Bernard and Thomas (1989) | latest standardized unexpected earnings (SUE), used for three months |

## Method

- **Universe**: at each month-end, US common stocks ranked by market cap, one share class per company,
  price of at least $1. Two versions: the largest 500 and the largest 3000. Delisted stocks are included.
- **Portfolios**: sort the universe into 10 groups by score, hold each group for the next month with
  equal weights. Long-short = top group minus bottom group.
- **Data**: prices, market caps, fundamentals and 8-K dates from Sharadar; daily factors from the
  Ken French data library.

## Results so far (before costs)

Long-short portfolios, equal-weighted, monthly rebalancing:

| Signal | Universe | First month | Mean per month | t-stat | Annual Sharpe | Max drawdown |
|---|---|---|---|---|---|---|
| Momentum | top 500 | 1999-01 | 0.91% | 1.93 | 0.37 | -73% |
| Momentum | top 3000 | 1999-01 | 0.72% | 1.59 | 0.30 | -77% |
| Short-term reversal | top 500 | 1999-01 | -0.11% | -0.29 | -0.05 | -78% |
| Short-term reversal | top 3000 | 1999-01 | 0.10% | 0.28 | 0.05 | -60% |
| Low volatility | top 500 | 1999-01 | 0.36% | 0.80 | 0.15 | -70% |
| Low volatility | top 3000 | 1999-01 | 0.59% | 1.25 | 0.24 | -74% |
| Post-earnings drift | top 500 | 2000-09 | 0.37% | 1.97 | 0.39 | -34% |
| Post-earnings drift | top 3000 | 2000-09 | 0.38% | 2.19 | 0.43 | -31% |

![Growth of $1 in each long-short portfolio](results/cumulative_returns.png)

## Validation

Momentum and reversal decile returns were compared month by month with the Ken French
"10 Portfolios Formed on Momentum / Short-Term Reversal" files. Long-short correlation is 0.81 to 0.83
for equal-weighted portfolios (his sample includes micro-caps, ours does not) and 0.94 for
value-weighted portfolios.

That comparison exposed errors in the vendor's market cap: for a few small stocks around 2000 to 2003
the share count taken from one SEC filing is wrong by a factor of 100 or more until the next filing.
Equal-weighted results are barely affected, which is one reason the portfolios are equal-weighted.

## Known limitations

- No transaction costs yet.
- No delisting returns: a stock with no price in the holding month is dropped (about 0.4% of stock-months).
- The security category (common stock, ADR, ...) is the current one, not point-in-time.
- PEAD uses last year's same quarter as expected earnings, not analyst forecasts. Before 2004 the
  announcement date is the 10-Q/10-K filing date, because earnings 8-Ks did not exist yet.
- Fama-French factors end 2026-09, so the last low-volatility formation month is September 2026.

## How to run

```
uv sync
# put SHARADAR_API_KEY=... in .env
uv run python -m anomaly_replication.loader      # download data into data/ (about 2.5 GB)
uv run python -m anomaly_replication.backtest    # writes results/
```

## Code

| File | Content |
|---|---|
| `loader.py` | download Sharadar tables and Fama-French factors, read them from parquet |
| `universe.py` | monthly universe of the largest N common stocks |
| `returns.py` | monthly and daily return tables |
| `signals.py` | the four signal definitions |
| `portfolio.py` | decile sort, long-short return, summary statistics |
| `backtest.py` | runs everything and writes `results/` |

Data from [Sharadar.com](https://sharadar.com/). Raw data is not included in this repository.
