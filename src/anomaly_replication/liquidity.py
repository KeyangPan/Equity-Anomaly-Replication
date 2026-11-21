"""How much each stock trades: its average daily dollar volume.

One value per stock and month (rows: month-end date, columns: ticker), so it can be lined up
with the portfolio holdings. The size of an order relative to this volume drives its price impact.
"""

import pandas as pd

from anomaly_replication.loader import load_table


def average_dollar_volume(tickers: list[str], months: int = 3) -> pd.DataFrame:
    """Return each stock's average daily dollar volume over the latest `months` months, in dollars.

    Daily dollar volume = close * volume. Both are adjusted for splits in the same way,
    so the product is the true dollar amount traded that day.
    """
    bars = load_table("stocks", ["ticker", "date", "close", "volume"])
    bars = bars[bars["ticker"].isin(tickers)]
    dollar_volume = bars["close"] * bars["volume"]
    month = bars["date"].dt.to_period("M")

    monthly = dollar_volume.groupby([bars["ticker"], month]).mean().unstack("ticker")
    monthly = monthly.rolling(months, min_periods=1).mean()

    # Label each row by the market's last trading day of that month, like the other monthly tables.
    month_end_date = bars.groupby(month)["date"].max()
    monthly.index = monthly.index.map(month_end_date)
    monthly.index.name = "date"
    return monthly


def fix_volume_errors(volume: pd.DataFrame, universe: pd.DataFrame, min_turnover: float = 0.0002) -> pd.DataFrame:
    """Replace dollar volumes that cannot be right, and return the table with the same shape.

    The vendor's volume is wrong for a few stocks: for example a stock among the 500 largest
    with $70 traded per day. Two repairs, month by month:

    1. A stock in the universe has a market cap, so we look at
       turnover = daily dollar volume / market cap, typically 0.5% to 1% per day.
       Where it is below `min_turnover` (0.02% per day: 20 years to trade the company once)
       or missing, the volume is replaced by market cap * the universe's median turnover.
    2. A stock outside the universe (one that just dropped out, which we may still be selling)
       has no market cap here. Its volume is raised to at least the 5th percentile of the
       universe's volumes, and set to that level when missing.
    """
    market_cap = universe.pivot(index="date", columns="ticker", values="marketcap") * 1e6  # stored in $ millions
    market_cap = market_cap.reindex(index=volume.index, columns=volume.columns)
    in_universe = market_cap.notna()

    turnover = volume / market_cap
    typical_volume = market_cap.mul(turnover.median(axis=1), axis=0)
    fixed = volume.mask(in_universe & ~(turnover >= min_turnover), typical_volume)

    low_volume = fixed.where(in_universe).quantile(0.05, axis=1)
    floor = pd.DataFrame({ticker: low_volume for ticker in fixed.columns})
    return fixed.mask(~in_universe & ~(fixed >= floor), floor)
