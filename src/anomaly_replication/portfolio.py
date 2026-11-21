"""Turn a signal into decile portfolios and a long-short return series."""

import numpy as np
import pandas as pd


def assign_groups(
    signal: pd.DataFrame,
    universe: pd.DataFrame,
    monthly_returns: pd.DataFrame,
    n_groups: int = 10,
    min_coverage: float = 0.5,
) -> pd.DataFrame:
    """Return one row per (date, ticker) with the stock's score, next-month return and group.

    signal:          wide table of scores (rows: month-end date t, columns: ticker).
    universe:        output of build_universe(), the stocks allowed at each date t.
    monthly_returns: wide table of monthly returns.

    At each month-end t the universe stocks that have a score are sorted by score into `n_groups`
    equal-sized groups (group 1 = lowest score, group `n_groups` = highest).

    A month is skipped when less than `min_coverage` of the universe has a score
    (this happens for PEAD before 2001, when few stocks have enough EPS history).
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
    return table


def next_month_end(monthly_returns: pd.DataFrame) -> dict:
    """Map each month-end t to the following month-end t+1 (the end of the holding month)."""
    all_dates = monthly_returns.index
    return dict(zip(all_dates[:-1], all_dates[1:]))


def sort_into_portfolios(
    signal: pd.DataFrame,
    universe: pd.DataFrame,
    monthly_returns: pd.DataFrame,
    n_groups: int = 10,
    min_coverage: float = 0.5,
) -> pd.DataFrame:
    """Return the monthly return of each signal group, plus the long-short return.

    The groups come from assign_groups(). Each group is held during month t+1 with equal weight
    on every stock.

    Output rows are the holding month t+1. Columns are 1..n_groups and "long_short",
    where long_short = top group - bottom group.
    """
    table = assign_groups(signal, universe, monthly_returns, n_groups, min_coverage)

    # Equal-weighted return of each group: the plain average over its stocks.
    group_returns = table.groupby(["date", "group"])["next_return"].mean().unstack("group")

    group_returns["long_short"] = group_returns[n_groups] - group_returns[1]

    # Relabel rows from the formation date t to the holding month-end t+1.
    group_returns.index = group_returns.index.map(next_month_end(monthly_returns))
    return group_returns


def long_short_weights(
    signal: pd.DataFrame,
    universe: pd.DataFrame,
    monthly_returns: pd.DataFrame,
    n_groups: int = 10,
    min_coverage: float = 0.5,
) -> pd.DataFrame:
    """Return the holdings of the long-short portfolio (rows: formation date t, columns: ticker).

    A stock of the top group has weight +1/N, a stock of the bottom group -1/M (N and M are the
    numbers of stocks in the two groups), every other stock 0. So the long side adds up to +$1
    and the short side to -$1. These are the same holdings as in sort_into_portfolios():
    the sum of weight * next-month return is its long_short return.
    """
    table = assign_groups(signal, universe, monthly_returns, n_groups, min_coverage)
    group_size = table.groupby(["date", "group"])["ticker"].transform("size")
    side = (table["group"] == n_groups).astype(int) - (table["group"] == 1).astype(int)  # +1, -1 or 0
    table["weight"] = side / group_size

    held = table[table["weight"] != 0]
    return held.pivot(index="date", columns="ticker", values="weight").fillna(0.0)


def combine_weights(weights: list[pd.DataFrame]) -> pd.DataFrame:
    """Return the holdings of a portfolio that combines the given portfolios with equal capital.

    Step 1: weight of a stock = average of its weights in the portfolios. A stock that one
            portfolio buys and another one shorts partly cancels, so after this step the long
            side is less than $1 (about $0.76 for our four signals) and so is the short side.
    Step 2: scale the long side back up to +$1 and the short side to -$1, month by month.

    So the result is again a $1 long / $1 short portfolio, like each of its parts.
    Only the dates that all portfolios have are kept.
    """
    dates = weights[0].index
    for table in weights[1:]:
        dates = dates.intersection(table.index)
    total = weights[0].loc[dates]
    for table in weights[1:]:
        total = total.add(table.loc[dates], fill_value=0.0)
    average = total / len(weights)

    long_side, short_side = average.clip(lower=0), average.clip(upper=0)
    long_side = long_side.div(long_side.sum(axis=1), axis=0)
    short_side = short_side.div(-short_side.sum(axis=1), axis=0)
    return long_side + short_side


def portfolio_returns(weights: pd.DataFrame, monthly_returns: pd.DataFrame) -> pd.Series:
    """Return the monthly return of the given holdings (rows: the holding month).

    return of the month = sum over stocks of weight * the stock's return in the holding month
    """
    next_returns = monthly_returns.shift(-1).reindex(index=weights.index, columns=weights.columns).fillna(0.0)
    returns = (weights * next_returns).sum(axis=1)
    returns.index = returns.index.map(next_month_end(monthly_returns))
    return returns


def weight_changes(weights: pd.DataFrame, monthly_returns: pd.DataFrame) -> pd.DataFrame:
    """Return the trade in every stock at each rebalance (rows: formation date t, columns: ticker).

    `weights` are holdings per $1 of long capital and $1 of short capital, as returned by
    long_short_weights(). During the month a position of w dollars grows to w * (1 + r).
    At the next month-end we trade from those drifted positions to the new weights:

        trade_t = new weight_t - old weight_{t-1} * (1 + return of the month)

    Positive = buy, negative = sell. The first row is the initial purchase.
    """
    next_returns = monthly_returns.shift(-1).reindex(index=weights.index, columns=weights.columns).fillna(0.0)
    drifted = weights * (1 + next_returns)          # positions one month later, before trading
    held_before = drifted.shift(1).fillna(0.0)      # what we hold when we arrive at each rebalance
    return weights - held_before


def dollars_traded(weights: pd.DataFrame, monthly_returns: pd.DataFrame) -> pd.Series:
    """Return the dollars traded at each rebalance (rows: the holding month that follows it).

        traded_t = sum over stocks of | trade_t |      (see weight_changes)

    Buys and sells both count. Replacing every stock on both sides gives about 4: sell $1 and
    buy $1 on the long side, cover $1 and short $1 on the short side. Keeping everything gives
    about 0. The first month counts the initial purchase (2).
    """
    traded = weight_changes(weights, monthly_returns).abs().sum(axis=1)

    # Relabel from the formation date t to the holding month-end t+1, like the returns.
    traded.index = traded.index.map(next_month_end(monthly_returns))
    return traded


def dollars_short(weights: pd.DataFrame, monthly_returns: pd.DataFrame) -> pd.Series:
    """Return the dollars held short during each holding month (rows: the holding month).

    `weights` are holdings as returned by long_short_weights() or combine_weights():
    the short side is $1.
    """
    shorted = -weights.clip(upper=0).sum(axis=1)
    shorted.index = shorted.index.map(next_month_end(monthly_returns))
    return shorted


def summarize_costs(
    gross_returns: pd.Series,
    traded: pd.Series,
    shorted: pd.Series,
    cost_bps: float,
    borrow_fee_pct: float,
) -> pd.Series:
    """Summary of a long-short portfolio after transaction costs and stock-borrowing fees.

    Trading cost: every dollar traded costs `cost_bps` basis points (1 bp = 0.01%), buys and
    sells alike. Borrowing fee: to short a stock we borrow it, and its owner charges
    `borrow_fee_pct` percent per year on the dollars held short.

        trading cost of the month = traded * cost_bps / 10000
        borrow cost of the month  = shorted * borrow_fee_pct / 100 / 12
        net return                = gross return - trading cost - borrow cost

    "turnover" is the share of each side replaced per month: traded / 4, so 100% means that
    every stock of both sides is replaced. "break-even cost" is the trading cost per dollar
    traded at which the average net return is exactly zero, after paying the borrowing fee:
    (average gross return - average borrow cost) / average traded.
    """
    both = pd.concat([gross_returns, traded, shorted], axis=1, keys=["gross", "traded", "shorted"]).dropna()
    trading_cost = both["traded"] * cost_bps / 10000
    borrow_cost = both["shorted"] * borrow_fee_pct / 100 / 12
    net = both["gross"] - trading_cost - borrow_cost
    n_months = len(both)
    return pd.Series(
        {
            "turnover % per month": 100 * both["traded"].mean() / 4,
            "gross mean % per month": 100 * both["gross"].mean(),
            "trading cost % per month": 100 * trading_cost.mean(),
            "borrow cost % per month": 100 * borrow_cost.mean(),
            "net mean % per month": 100 * net.mean(),
            "net t-stat": net.mean() / (net.std() / np.sqrt(n_months)),
            "net NW t-stat": newey_west_t_stat(net),
            "net annual Sharpe": net.mean() / net.std() * np.sqrt(12),
            "net max drawdown %": 100 * max_drawdown(net),
            "break-even cost bps": 10000 * (both["gross"].mean() - borrow_cost.mean()) / both["traded"].mean(),
        }
    )


def max_drawdown(returns: pd.Series) -> float:
    """Largest peak-to-trough loss of the cumulative value of a monthly return series."""
    value = (1 + returns.dropna()).cumprod()
    drawdown = value / value.cummax() - 1
    return drawdown.min()


def newey_west_t_stat(returns: pd.Series, lags: int | None = None) -> float:
    """t-stat of the mean that allows for autocorrelation (Newey and West 1987).

    The plain t-stat, mean / (std / sqrt(T)), assumes the months are independent. When neighbouring
    months hold many of the same stocks (post-earnings drift reuses a surprise for three
    month-ends, momentum changes slowly), their returns are correlated and the plain t-stat is too
    large when the correlation is positive. Newey-West adds the autocovariances up to `lags` months
    to the variance of the mean, with weights that fall linearly from 1 (Bartlett weights):

        var(mean) = (gamma_0 + 2 * sum_{j=1..L} (1 - j / (L + 1)) * gamma_j) / T
        gamma_j   = sum_t (r_t - mean) (r_{t-j} - mean) / T

    Default L = floor(4 * (T / 100) ** (2 / 9)), the usual rule (4 for T = 120 or 250 months),
    which also covers post-earnings drift's three-month overlap.
    """
    values = returns.dropna().to_numpy()
    n_months = len(values)
    if lags is None:
        lags = int(np.floor(4 * (n_months / 100) ** (2 / 9)))
    demeaned = values - values.mean()
    variance = demeaned @ demeaned / n_months
    for lag in range(1, lags + 1):
        autocovariance = demeaned[lag:] @ demeaned[:-lag] / n_months
        variance += 2 * (1 - lag / (lags + 1)) * autocovariance
    return values.mean() / np.sqrt(variance / n_months)


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
            "NW t-stat": newey_west_t_stat(returns),
            "annual Sharpe": mean / std * np.sqrt(12),
            "max drawdown %": 100 * max_drawdown(returns),
        }
    )


def market_beta(returns: pd.Series, market_returns: pd.Series) -> float:
    """Slope of a regression of a monthly return series on the market's monthly excess return.

    beta = cov(r, MKT) / var(MKT), over the months where both exist.
    A market-neutral portfolio has a beta close to 0.
    """
    both = pd.concat([returns, market_returns], axis=1, keys=["portfolio", "market"]).dropna()
    return both["portfolio"].cov(both["market"]) / both["market"].var()
