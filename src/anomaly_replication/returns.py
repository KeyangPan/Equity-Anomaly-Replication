"""Monthly and daily returns for every stock.

All tables are "wide": one row per date, one column per ticker.
"""

import pandas as pd

from anomaly_replication.loader import load_table


def build_monthly_prices() -> pd.DataFrame:
    """Return each stock's last adjusted close in every month (rows: month-end date, columns: ticker).

    `closeadj` is adjusted for splits and dividends, so the ratio of two prices is a total return.
    A stock that stops trading mid-month keeps its last traded price for that month.
    """
    prices = load_table("stocks", ["ticker", "date", "closeadj"])
    prices = prices.sort_values("date")
    prices["month"] = prices["date"].dt.to_period("M")

    last_price_in_month = prices.groupby(["ticker", "month"])["closeadj"].last()
    monthly_prices = last_price_in_month.unstack("ticker")

    # Label each row by the market's last trading day of that month (same dates as the universe).
    month_end_date = prices.groupby("month")["date"].max()
    monthly_prices.index = monthly_prices.index.map(month_end_date)
    monthly_prices.index.name = "date"

    # The latest month in the data is usually still in progress, so drop it.
    return monthly_prices.iloc[:-1]


def build_monthly_returns(monthly_prices: pd.DataFrame) -> pd.DataFrame:
    """Return r_t = P_t / P_{t-1} - 1, the total return of each stock in each month."""
    return monthly_prices / monthly_prices.shift(1) - 1


def build_daily_returns(tickers: list[str]) -> pd.DataFrame:
    """Return the daily total return of the given tickers (rows: trading date, columns: ticker)."""
    prices = load_table("stocks", ["ticker", "date", "closeadj"])
    prices = prices[prices["ticker"].isin(tickers)]
    daily_prices = prices.pivot(index="date", columns="ticker", values="closeadj")
    return daily_prices / daily_prices.shift(1) - 1


def build_monthly_market_returns(factors: pd.DataFrame) -> pd.Series:
    """Return the market's monthly excess return (index: month-end date), from the daily factors.

    Daily market return = Mkt-RF + RF. We compound the market and the risk-free rate
    over each month and take the difference.
    """
    month_of_date = factors.index.to_period("M")
    market = (1 + factors["Mkt-RF"] + factors["RF"]).groupby(month_of_date).prod() - 1
    risk_free = (1 + factors["RF"]).groupby(month_of_date).prod() - 1
    excess = market - risk_free

    # Label each month by its last trading day, like the other monthly tables.
    month_end_date = factors.index.to_series().groupby(month_of_date).max()
    excess.index = excess.index.map(month_end_date)
    excess.index.name = "date"
    return excess
