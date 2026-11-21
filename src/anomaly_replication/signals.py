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


# Post-earnings drift: how long a surprise is used and how fast it fades.
PEAD_MONTHS_VALID = 3  # a surprise is used at no more than 3 month-ends
PEAD_HALF_LIFE_DAYS = 20  # it counts 50% after 20 days, 25% after 40, ...
PEAD_MAX_AGE_DAYS = 90  # and not at all once it is older than 90 days


def post_earnings_drift(month_ends: pd.DatetimeIndex) -> pd.DataFrame:
    """Post-earnings-announcement drift (Bernard and Thomas 1989): the latest earnings surprise,
    fading with its age.

    With x = calendar days from the most recent announcement before month-end t to month-end t:

        score_t = SUE * 0.5 ** (x / PEAD_HALF_LIFE_DAYS)

    The surprise counts half after 20 days, a quarter after 40, and so on. An old surprise is
    pulled towards zero, the middle of the cross-section, so the top and bottom groups hold mostly
    recent announcements.

    No score when the announcement is more than PEAD_MONTHS_VALID month-ends old or more than
    PEAD_MAX_AGE_DAYS days old. Three months is roughly the 60 trading days over which the paper
    measures the drift. (The paper keeps the full surprise for the whole window; we do not test
    that version.)
    """
    announcements = load_earnings_announcements()

    # The first month-end strictly after the announcement: the first date we may trade on it.
    position = month_ends.searchsorted(announcements["announcement_date"], side="right")
    announcements = announcements[position < len(month_ends)].copy()
    announcements["date"] = month_ends[position[position < len(month_ends)]]

    # If a stock has two announcements before the same month-end, keep the later one.
    announcements = announcements.sort_values("announcement_date")
    announcements = announcements.drop_duplicates(["date", "ticker"], keep="last")

    # Carry each surprise, and its announcement date, forward to PEAD_MONTHS_VALID month-ends in total.
    def carry_forward(values: str) -> pd.DataFrame:
        table = announcements.pivot(index="date", columns="ticker", values=values)
        table = table.reindex(month_ends).ffill(limit=PEAD_MONTHS_VALID - 1)
        table.index.name = "date"
        return table

    sue = carry_forward("sue")
    announced = carry_forward("announcement_date")
    days_since = announced.rsub(month_ends.to_series(), axis=0).apply(lambda column: column.dt.days)

    sue = sue.where(days_since <= PEAD_MAX_AGE_DAYS)
    return sue * 0.5 ** (days_since / PEAD_HALF_LIFE_DAYS)
