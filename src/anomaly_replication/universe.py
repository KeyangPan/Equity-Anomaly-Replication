"""Build the monthly stock universe: the 3000 largest US common stocks at each month-end.

Usage:
    uv run python -m anomaly_replication.universe    # print a summary
    universe = build_universe()                      # use in a notebook
"""

import pandas as pd

from anomaly_replication.loader import load_table

# Sharadar categories we keep. ADRs, Canadian stocks, preferred stock and funds are excluded.
# "Secondary Class" (e.g. GOOG next to GOOGL) is excluded so each company is counted once.
COMMON_STOCK_CATEGORIES = [
    "Domestic Common Stock",
    "Domestic Common Stock Primary Class",
]


def get_month_end_dates(trading_dates: pd.Series) -> pd.Series:
    """Return the last trading day of every complete calendar month."""
    trading_dates = pd.Series(trading_dates.unique()).sort_values()
    month_of_date = trading_dates.dt.to_period("M")
    month_ends = trading_dates.groupby(month_of_date).max()
    # The latest month in the data is usually still in progress, so drop it.
    return month_ends.iloc[:-1].reset_index(drop=True)


def build_universe(n_stocks: int = 3000, min_price: float = 1.0) -> pd.DataFrame:
    """Return one row per (month-end date, ticker) for the stocks in the universe.

    At each month-end, using only that day's data:
      1. keep US common stocks (one share class per company),
      2. keep stocks with a market cap and a price of at least `min_price` dollars,
      3. rank by market cap and keep the largest `n_stocks`.

    Columns: date, ticker, marketcap (USD millions), price (unadjusted close), rank (1 = largest).
    """
    tickers = load_table("tickers", ["table", "ticker", "category"])
    is_stock_table = tickers["table"] == "SEP"
    is_common_stock = tickers["category"].isin(COMMON_STOCK_CATEGORIES)
    common_stock_tickers = tickers.loc[is_stock_table & is_common_stock, "ticker"]

    prices = load_table("stocks", ["ticker", "date", "closeunadj"])
    prices = prices.rename(columns={"closeunadj": "price"})
    month_ends = get_month_end_dates(prices["date"])

    marketcaps = load_table("daily", ["ticker", "date", "marketcap"])
    marketcaps = marketcaps[marketcaps["date"].isin(month_ends)]
    marketcaps = marketcaps[marketcaps["ticker"].isin(common_stock_tickers)]

    # Inner merge: a stock must have both a market cap and a price on the month-end date.
    candidates = marketcaps.merge(prices, on=["ticker", "date"], how="inner")
    candidates = candidates[candidates["marketcap"] > 0]
    candidates = candidates[candidates["price"] >= min_price]

    candidates["rank"] = (
        candidates.groupby("date")["marketcap"].rank(ascending=False, method="first").astype(int)
    )
    universe = candidates[candidates["rank"] <= n_stocks]
    universe = universe.sort_values(["date", "rank"]).reset_index(drop=True)
    return universe[["date", "ticker", "marketcap", "price", "rank"]]


if __name__ == "__main__":
    universe = build_universe()
    stocks_per_month = universe.groupby("date").size()
    smallest_marketcap = universe.groupby("date")["marketcap"].min()

    print(f"months: {len(stocks_per_month)}  ({universe['date'].min().date()} to {universe['date'].max().date()})")
    print(f"stocks per month: min {stocks_per_month.min()}, max {stocks_per_month.max()}")
    print(f"distinct tickers ever in the universe: {universe['ticker'].nunique():,}")
    print("\nSmallest market cap in the universe (USD millions), every 5th December:")
    decembers = smallest_marketcap[smallest_marketcap.index.month == 12]
    print(decembers.iloc[::5].round(0).to_string())
    print("\nTen largest stocks at the latest month-end:")
    latest = universe[universe["date"] == universe["date"].max()]
    print(latest.head(10).to_string(index=False))
