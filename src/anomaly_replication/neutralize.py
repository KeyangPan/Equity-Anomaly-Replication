"""Remove the sector and market-beta part of a signal.

A raw signal often bets on a whole sector or on the market: in 1999 the momentum winners were mostly
technology stocks, and low-volatility stocks are mostly low-beta stocks. Neutralizing keeps only the
part of the score that compares a stock with similar stocks: same sector, same beta.
"""

import numpy as np
import pandas as pd

from anomaly_replication.loader import load_table


def load_sectors() -> pd.Series:
    """Return the sector of every ticker (index: ticker), one of 11 sectors such as "Technology".

    This is the sector Sharadar assigns today, not the sector the company had at each past date.
    """
    tickers = load_table("tickers", ["table", "ticker", "sector"])
    tickers = tickers[tickers["table"] == "SEP"]
    sectors = tickers.set_index("ticker")["sector"]
    return sectors[sectors.notna() & (sectors != "")]


def estimate_betas(
    daily_returns: pd.DataFrame,
    factors: pd.DataFrame,
    month_ends: pd.DatetimeIndex,
    window: int = 252,
    min_days: int = 120,
) -> pd.DataFrame:
    """Return each stock's market beta at each month-end (rows: month-end date, columns: ticker).

    Beta is the slope of a regression of the stock's daily excess return on the market's
    daily excess return, over the latest `window` trading days (252 = one year):

        beta_i = cov(r_i - RF, MKT) / var(MKT)

    A stock needs at least `min_days` returns in the window. The covariance and the variance
    are both computed on the days where the stock has a return.
    """
    factors = factors.reindex(daily_returns.index).dropna()
    excess_returns = daily_returns.loc[factors.index].sub(factors["RF"], axis=0)
    market = factors["Mkt-RF"]

    betas = {}
    for month_end in month_ends:
        # The latest `window` trading days up to and including the month-end.
        stock_window = excess_returns.loc[:month_end].iloc[-window:]
        if len(stock_window) < min_days or stock_window.index[-1] != month_end:
            continue
        stock = stock_window.to_numpy()                                   # days x stocks
        market_column = market.loc[stock_window.index].to_numpy()[:, None]  # days x 1

        # Each stock uses only the days on which it has a return.
        has_return = ~np.isnan(stock)
        n_days = has_return.sum(axis=0)
        n_days = np.where(n_days >= min_days, n_days, np.nan)
        stock = np.where(has_return, stock, 0.0)
        market_on_stock_days = np.where(has_return, market_column, 0.0)

        mean_stock = stock.sum(axis=0) / n_days
        mean_market = market_on_stock_days.sum(axis=0) / n_days
        covariance = (stock * market_on_stock_days).sum(axis=0) / n_days - mean_stock * mean_market
        market_variance = (market_on_stock_days**2).sum(axis=0) / n_days - mean_market**2

        betas[month_end] = pd.Series(covariance / market_variance, index=excess_returns.columns)

    betas = pd.DataFrame(betas).T
    betas.index.name = "date"
    betas.columns.name = "ticker"
    return betas


def neutralize(
    signal: pd.DataFrame, universe: pd.DataFrame, sectors: pd.Series, betas: pd.DataFrame
) -> pd.DataFrame:
    """Return the signal with its sector and beta part removed (same wide shape as `signal`).

    At each month-end, separately inside each sector of the universe:
      1. rank the scores and scale the rank to between 0 and 1 (0 = lowest score of the sector,
         1 = highest),
      2. turn the rank into a z-score: subtract the sector's average rank and divide by the
         sector's standard deviation of ranks. Every sector now has mean 0 and standard deviation 1,
         so no sector is over-represented among the highest or lowest values.
      3. regress the z-score on beta, with the sector's own intercept a and slope c:

             z_i = a + c * beta_i + residual_i

         (betas beyond the month's 1st or 99th percentile are first pulled in to that percentile),
      4. the residual is the new score.

    Inside every sector the residual averages zero and is uncorrelated with beta. A high value
    means "high score compared with stocks of the same sector and the same beta".

    A stock without a sector or a beta gets no score.
    """
    # Long format: one row per (date, ticker) with score, sector and beta.
    table = universe[["date", "ticker"]].merge(
        signal.stack().rename("score").reset_index(), on=["date", "ticker"]
    )
    table = table.merge(betas.stack().rename("beta").reset_index(), on=["date", "ticker"])
    table["sector"] = table["ticker"].map(sectors)
    table = table.dropna(subset=["score", "beta", "sector"])

    # Rank inside each (date, sector): (position - 0.5) / number of stocks, so the middle stock gets 0.5.
    by_sector = table.groupby(["date", "sector"])["score"]
    table["rank"] = (by_sector.rank() - 0.5) / by_sector.transform("size")

    # Z-score of the rank inside each (date, sector): mean 0 and standard deviation 1 in every sector.
    rank_by_sector = table.groupby(["date", "sector"])["rank"]
    table["z"] = (table["rank"] - rank_by_sector.transform("mean")) / rank_by_sector.transform("std")

    # A few estimated betas are extreme (data errors, tiny stocks). Pull them in to the
    # 1st and 99th percentile of the month so they do not drive the regression.
    lowest = table.groupby("date")["beta"].transform(lambda beta: beta.quantile(0.01))
    highest = table.groupby("date")["beta"].transform(lambda beta: beta.quantile(0.99))
    table["beta"] = table["beta"].clip(lower=lowest, upper=highest)

    # One-variable regression inside each (date, sector), written out:
    #     c = sum(beta_dev * z_dev) / sum(beta_dev^2),   residual = z_dev - c * beta_dev
    # where "_dev" means the value minus its average in the (date, sector) group.
    groups = [table["date"], table["sector"]]
    beta_dev = table["beta"] - table["beta"].groupby(groups).transform("mean")
    z_dev = table["z"] - table["z"].groupby(groups).transform("mean")
    slope = (beta_dev * z_dev).groupby(groups).transform("sum") / (beta_dev**2).groupby(groups).transform("sum")
    table["neutral_score"] = z_dev - slope * beta_dev
    table = table.dropna(subset=["neutral_score"])  # a sector with a single stock has no slope

    return table.pivot(index="date", columns="ticker", values="neutral_score")
