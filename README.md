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
- **Neutral version**: each signal is also tested after removing its sector and market-beta part.
  At each month-end, inside each of 11 sectors, the score's rank is regressed on the stock's beta
  (252 trading days); the residual is the new score.
- **Data**: prices, market caps, fundamentals and 8-K dates from Sharadar; daily factors from the
  Ken French data library.

## Data range

Downloaded on 2026-11-15.

| Data | Source | First date | Last date |
|---|---|---|---|
| Daily prices | Sharadar | 1997-12-31 | 2026-11-13 |
| Daily market cap | Sharadar | 1998-12-01 | 2026-11-13 |
| Quarterly fundamentals | Sharadar | 1990-06 | 2026-11-13 |
| 8-K filings | Sharadar | 1993-11-08 | 2026-11-13 |
| Daily Fama-French factors | Ken French | 1926-07-01 | 2026-09-30 |

Market cap is the binding constraint: the first universe is formed on 1998-12-31, so the first
holding month is 1999-01. The last holding month is 2026-10.

| Signal | Holding months | Count |
|---|---|---|
| Momentum, short-term reversal, low volatility | 1999-01 to 2026-10 | 334 |
| Post-earnings drift | 2000-09 to 2026-10 | 310 |

Post-earnings drift has no portfolio in four months inside its range (2001-03, 2002-03, 2003-03,
2004-03): at the end of those Februaries less than half of the universe had a valid score.

## Results so far (before costs)

Long-short portfolios, equal-weighted, monthly rebalancing. "Ex-post beta" is the slope of the
realized long-short return on the market excess return over the full sample; it is measured after
the fact and is not the stock beta used to build the neutral scores.

| Signal | Universe | Version | Mean per month | t-stat | Annual Sharpe | Max drawdown | Ex-post beta |
|---|---|---|---|---|---|---|---|
| Momentum | top 500 | raw | 0.91% | 1.93 | 0.37 | -73% | -0.27 |
| | | neutral | 0.43% | 2.17 | 0.41 | -41% | -0.15 |
| Momentum | top 3000 | raw | 0.72% | 1.59 | 0.30 | -77% | -0.52 |
| | | neutral | 0.37% | 1.63 | 0.31 | -58% | -0.20 |
| Short-term reversal | top 500 | raw | -0.11% | -0.29 | -0.05 | -78% | 0.34 |
| | | neutral | 0.28% | 1.68 | 0.32 | -43% | 0.15 |
| Short-term reversal | top 3000 | raw | 0.10% | 0.28 | 0.05 | -60% | 0.47 |
| | | neutral | 0.50% | 2.54 | 0.48 | -31% | 0.21 |
| Low volatility | top 500 | raw | 0.36% | 0.80 | 0.15 | -70% | -0.99 |
| | | neutral | 0.35% | 2.31 | 0.44 | -34% | -0.08 |
| Low volatility | top 3000 | raw | 0.59% | 1.25 | 0.24 | -74% | -1.25 |
| | | neutral | 0.36% | 1.79 | 0.34 | -43% | -0.27 |
| Post-earnings drift | top 500 | raw | 0.37% | 1.97 | 0.39 | -34% | -0.23 |
| | | neutral | 0.16% | 1.34 | 0.26 | -22% | -0.08 |
| Post-earnings drift | top 3000 | raw | 0.38% | 2.19 | 0.43 | -31% | -0.27 |
| | | neutral | 0.29% | 2.89 | 0.57 | -17% | -0.08 |

Raw signals:

![Growth of $1 in each raw long-short portfolio](results/cumulative_returns.png)

Sector- and beta-neutral signals:

![Growth of $1 in each neutral long-short portfolio](results/cumulative_returns_neutral.png)

`notebooks/results.ipynb` explains how each signal is built, why it might work, and walks through
the results.

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
- The security category (common stock, ADR, ...) and the sector are the current ones, not point-in-time.
- Neutralizing the score does not make the portfolio exactly neutral; the remaining ex-post beta is in the table.
- The neutral results depend on the exact method: neutral momentum in the top 3000 has a t-stat of 2.4
  with one beta slope for all sectors and 1.6 with one slope per sector (the version used).
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
| `neutralize.py` | market betas, sector- and beta-neutral scores |
| `portfolio.py` | decile sort, long-short return, summary statistics, market beta |
| `backtest.py` | runs everything and writes `results/` |

Data from [Sharadar.com](https://sharadar.com/). Raw data is not included in this repository.
