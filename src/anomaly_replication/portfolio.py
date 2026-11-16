"""Turn a signal into decile portfolios and a long-short return series."""

import numpy as np
import pandas as pd


def sort_into_portfolios(
    signal: pd.DataFrame,
    universe: pd.DataFrame,
    monthly_returns: pd.DataFrame,
    n_groups: int = 10,
    min_coverage: float = 0.5,
) -> pd.DataFrame:
    """Return the monthly return of each signal group, plus the long-short return.

    signal:          wide table of scores (rows: month-end date t, columns: ticker).
    universe:        output of build_universe(), the stocks allowed at each date t.
    monthly_returns: wide table of monthly returns.

    At each month-end t:
      1. take the universe stocks that have a score,
      2. sort them by score into `n_groups` equal-sized groups (group 1 = lowest score),
      3. hold each group during month t+1 with equal weight on every stock.

    A month is skipped when less than `min_coverage` of the universe has a score
    (this happens for PEAD before 2001, when few stocks have enough EPS history).

    Output rows are the holding month t+1. Columns are 1..n_groups and "long_short",
    where long_short = top group - bottom group.
    """
    # Long format: one row per (date, ticker).
    scores = signal.stack().rename("score").reset_index()
    next_month_returns = monthly_returns.shift(-1).stack().rename("next_return").reset_index()

    table = universe.merge(scores, on=["date", "ticker"])
    table = table.merge(next_month_returns, on=["date", "ticker"])
    table = table.dropna(subset=["score", "next_return"])

    # Coverage = stocks with a score / stocks in the universe, month by month.
    universe_size = universe.groupby("date")["ticker"].size()
    scored_size = table.groupby("date")["ticker"].size()
    coverage = scored_size / universe_size
    months_to_keep = coverage.index[coverage >= min_coverage]
    table = table[table["date"].isin(months_to_keep)].copy()

    # Group number from the score's percentile rank within the month: 1 = lowest, n_groups = highest.
    percentile = table.groupby("date")["score"].rank(pct=True, method="first")
    table["group"] = np.ceil(percentile * n_groups).astype(int)

    # Equal-weighted return of each group: the plain average over its stocks.
    group_returns = table.groupby(["date", "group"])["next_return"].mean().unstack("group")

    group_returns["long_short"] = group_returns[n_groups] - group_returns[1]

    # Relabel rows from the formation date t to the holding month-end t+1.
    all_dates = monthly_returns.index
    next_date = dict(zip(all_dates[:-1], all_dates[1:]))
    group_returns.index = group_returns.index.map(next_date)
    return group_returns


def max_drawdown(returns: pd.Series) -> float:
    """Largest peak-to-trough loss of the cumulative value of a monthly return series."""
    value = (1 + returns.dropna()).cumprod()
    drawdown = value / value.cummax() - 1
    return drawdown.min()


def summarize(returns: pd.Series) -> pd.Series:
    """Summary statistics of a monthly return series."""
    returns = returns.dropna()
    n_months = returns.count()
    mean = returns.mean()
    std = returns.std()
    return pd.Series(
        {
            "first month": returns.index.min().strftime("%Y-%m"),
            "months": n_months,
            "mean % per month": 100 * mean,
            "t-stat": mean / (std / np.sqrt(n_months)),
            "annual Sharpe": mean / std * np.sqrt(12),
            "max drawdown %": 100 * max_drawdown(returns),
        }
    )
