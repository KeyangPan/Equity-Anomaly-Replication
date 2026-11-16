"""Signal definitions. Each function returns a wide table of scores (rows: month-end date t, columns: ticker).

Convention: a higher score means we expect a higher return in month t+1,
so the long-short portfolio always buys the top group and shorts the bottom group.

The backtest that uses these signals is in backtest.py.
"""

import numpy as np
import pandas as pd

from anomaly_replication.loader import load_table


def momentum(monthly_prices: pd.DataFrame) -> pd.DataFrame:
    """Momentum (Jegadeesh and Titman 1993): return over the past 12 months, skipping the latest month.

    score_t = P_{t-1} / P_{t-12} - 1

    The latest month (t-1 to t) is skipped because one-month returns tend to reverse.
    """
    return monthly_prices.shift(1) / monthly_prices.shift(12) - 1


def short_term_reversal(monthly_prices: pd.DataFrame) -> pd.DataFrame:
    """Short-term reversal (Jegadeesh 1990): minus the return of the latest month.

    score_t = -(P_t / P_{t-1} - 1)

    The minus sign makes last month's losers the high-score stocks we buy.
    """
    return -(monthly_prices / monthly_prices.shift(1) - 1)


def low_volatility(daily_returns: pd.DataFrame, factors: pd.DataFrame, min_days: int = 15) -> pd.DataFrame:
    """Low idiosyncratic volatility (Ang, Hodrick, Xing and Zhang 2006).

    For each stock i and each month, regress its daily excess returns on the three Fama-French factors:

        r_{i,d} - RF_d = a_i + b_i * MKT_d + s_i * SMB_d + h_i * HML_d + e_{i,d}

    Idiosyncratic volatility is the standard deviation of the residuals e_{i,d} within the month:
    the part of the stock's daily moves that the three factors do not explain.

    score_t = -(idiosyncratic volatility in month t)

    The minus sign makes low-volatility stocks the high-score stocks we buy.
    A stock needs a return on every trading day of the month to get a score.
    """
    factor_names = ["Mkt-RF", "SMB", "HML"]
    factors = factors.reindex(daily_returns.index).dropna()
    excess_returns = daily_returns.loc[factors.index].sub(factors["RF"], axis=0)

    scores = {}
    month_of_date = excess_returns.index.to_period("M")
    for _, month_returns in excess_returns.groupby(month_of_date):
        month_returns = month_returns.dropna(axis=1)  # keep stocks with a return on every day
        n_days = len(month_returns)
        if n_days < min_days:
            continue

        # X has one row per day: a constant and the three factor returns.
        X = np.column_stack([np.ones(n_days), factors.loc[month_returns.index, factor_names]])
        Y = month_returns.to_numpy()  # one column per stock

        # Least squares for all stocks at once: each column of Y is regressed on X.
        coefficients = np.linalg.lstsq(X, Y, rcond=None)[0]
        residuals = Y - X @ coefficients
        n_coefficients = X.shape[1]
        idiosyncratic_volatility = residuals.std(axis=0, ddof=n_coefficients)

        month_end = month_returns.index.max()
        scores[month_end] = pd.Series(-idiosyncratic_volatility, index=month_returns.columns)

    signal = pd.DataFrame(scores).T
    signal.index.name = "date"
    signal.columns.name = "ticker"
    return signal


def load_earnings_announcements() -> pd.DataFrame:
    """Return one row per quarterly earnings announcement: ticker, announcement_date, sue.

    SUE (standardized unexpected earnings) uses last year's same quarter as the expected value:

        change_q = EPS_q - EPS_{q-4}
        SUE_q    = change_q / std(change over the latest 8 quarters)

    The announcement date is the first 8-K with item 2.02 ("results of operations") filed after
    the quarter ended. If there is none (always the case before 2004), we use the 10-Q/10-K filing date.
    """
    # Quarterly EPS as first reported ("ARQ" = as reported, quarterly). EPS is split-adjusted by Sharadar.
    columns = ["ticker", "dimension", "calendardate", "reportperiod", "date", "epsdil"]
    quarters = load_table("fundamentals", columns)
    quarters = quarters[quarters["dimension"] == "ARQ"].dropna(subset=["epsdil"])
    quarters = quarters.rename(columns={"date": "filing_date", "epsdil": "eps"})
    quarters = quarters.sort_values("filing_date").drop_duplicates(["ticker", "calendardate"], keep="first")

    # EPS of the same quarter one year earlier: match on calendardate minus one year.
    last_year = quarters[["ticker", "calendardate", "eps"]].rename(columns={"eps": "eps_last_year"})
    last_year["calendardate"] = last_year["calendardate"] + pd.DateOffset(years=1)
    quarters = quarters.merge(last_year, on=["ticker", "calendardate"], how="left")
    quarters["change"] = quarters["eps"] - quarters["eps_last_year"]

    # Scale by how much this company's year-over-year change usually varies.
    quarters = quarters.sort_values(["ticker", "calendardate"])
    change_std = quarters.groupby("ticker")["change"].transform(
        lambda change: change.rolling(8, min_periods=6).std()
    )
    # Ignore a standard deviation below a tenth of a cent: dividing by ~0 gives meaningless values.
    quarters["sue"] = quarters["change"] / change_std.where(change_std > 0.001)

    # First earnings 8-K (event code 22 = item 2.02) after the quarter end.
    events = load_table("events")
    is_earnings_release = events["eventcodes"].str.split("|").apply(lambda codes: "22" in codes)
    releases = events.loc[is_earnings_release, ["ticker", "date"]].rename(columns={"date": "release_date"})
    quarters = pd.merge_asof(
        quarters.sort_values("reportperiod"),
        releases.sort_values("release_date"),
        left_on="reportperiod",
        right_on="release_date",
        by="ticker",
        direction="forward",
        allow_exact_matches=False,
    )
    # Use the 8-K date only if it is not later than the 10-Q/10-K filing date.
    has_release = quarters["release_date"] <= quarters["filing_date"]
    quarters["announcement_date"] = quarters["release_date"].where(has_release, quarters["filing_date"])

    announcements = quarters.dropna(subset=["sue"])
    return announcements[["ticker", "announcement_date", "sue"]].reset_index(drop=True)


def post_earnings_drift(month_ends: pd.DatetimeIndex, months_valid: int = 3) -> pd.DataFrame:
    """Post-earnings-announcement drift (Bernard and Thomas 1989): the latest earnings surprise.

    score_t = SUE of the most recent announcement made before month-end t,
              if that announcement is at most `months_valid` month-ends old.

    Three months is roughly the 60 trading days over which the paper measures the drift.
    """
    announcements = load_earnings_announcements()

    # The first month-end strictly after the announcement: the first date we may trade on it.
    position = month_ends.searchsorted(announcements["announcement_date"], side="right")
    announcements = announcements[position < len(month_ends)].copy()
    announcements["date"] = month_ends[position[position < len(month_ends)]]

    # If a stock has two announcements before the same month-end, keep the later one.
    announcements = announcements.sort_values("announcement_date")
    announcements = announcements.drop_duplicates(["date", "ticker"], keep="last")
    signal = announcements.pivot(index="date", columns="ticker", values="sue")

    # Carry each surprise forward so it is used at `months_valid` month-ends in total.
    signal = signal.reindex(month_ends)
    signal.index.name = "date"
    return signal.ffill(limit=months_valid - 1)
