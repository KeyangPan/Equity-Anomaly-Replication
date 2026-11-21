"""Run the long-short backtest of every signal on three universes and save the results.

Usage:
    uv run python -m anomaly_replication.backtest

Every signal is tested in two versions:
    raw       the score as defined in the paper
    neutral   the score after removing its sector and market-beta part (see neutralize.py)

Writes to results/:
    summary.csv                      one row per (version, signal, universe): mean, t-stat, Newey-West t-stat, Sharpe, ...
    long_short_returns.csv           monthly long-short return of every (version, signal, universe)
    decile_mean_returns.csv          average monthly return of each of the 10 groups
    cumulative_returns.png           growth of $1 in each raw long-short portfolio
    cumulative_returns_neutral.png   the same for the neutral version
    cumulative_returns_combined.png  the same for the four signals combined, raw and neutral
    summary_last_10_years.csv        summary.csv again, using only the last 120 months of returns
    cumulative_returns_neutral_last_10_years.png, cumulative_returns_combined_last_10_years.png
    summary_costs.csv                turnover, trading cost, borrowing fee, net return and break-even cost
    summary_costs_last_10_years.csv  the same, last 120 months
    long_short_returns_net.csv       monthly long-short returns after trading costs and borrowing fees
    cumulative_returns_combined_net.png   the neutral combined portfolio before and after costs

The summary and the long-short returns also contain "combined": equal capital in four long-short
portfolios, netted and scaled back to $1 long / $1 short (see combine_weights in portfolio.py).

All returns are before transaction costs.
"""

import matplotlib

matplotlib.use("Agg")  # draw to a file, no window needed
import matplotlib.pyplot as plt
import pandas as pd

from anomaly_replication.loader import PROJECT_DIR, load_table
from anomaly_replication.neutralize import estimate_betas, load_sectors, neutralize
from anomaly_replication.portfolio import (
    combine_weights,
    dollars_short,
    dollars_traded,
    long_short_weights,
    market_beta,
    portfolio_returns,
    sort_into_portfolios,
    summarize,
    summarize_costs,
)
from anomaly_replication.returns import (
    build_daily_returns,
    build_monthly_market_returns,
    build_monthly_prices,
    build_monthly_returns,
)
from anomaly_replication.signals import low_volatility, momentum, post_earnings_drift, short_term_reversal
from anomaly_replication.universe import build_universe

RESULTS_DIR = PROJECT_DIR / "results"

# Three universes: the largest 500 stocks (large caps, similar to the S&P 500), the largest 1000
# (large and mid caps) and the largest 3000 (adds small caps).
UNIVERSE_SIZES = {"top 500": 500, "top 1000": 1000, "top 3000": 3000}

# One fixed color per signal, used in every chart.
SIGNAL_COLORS = {
    "momentum": "#2a78d6",
    "short_term_reversal": "#eb6834",
    "low_volatility": "#1baf7a",
    "post_earnings_drift": "#eda100",
}

# Backtest sample: the holding months are SAMPLE_START to SAMPLE_END, so the first portfolio is
# formed at the end of 2005-12 and the last at the end of 2026-09. The signals still use the data
# before 2006 (momentum's 12 months, the betas' daily history, PEAD's earnings history).
SAMPLE_START = pd.Period("2006-01", "M")
SAMPLE_END = pd.Period("2026-10", "M")

# The results are also reported for the most recent months alone: 120 months = the last 10 years.
RECENT_MONTHS = 120
RECENT_PERIOD = "last 10 years"

# Four signals held together, with a quarter of the capital in each. Opposite positions in the
# same stock cancel, and what is left is scaled back to $1 long / $1 short.
COMBINED = "combined"
COMBINED_SIGNALS = ["momentum", "short_term_reversal", "low_volatility", "post_earnings_drift"]

# Transaction cost, in basis points (1 bp = 0.01%) per dollar traded, buys and sells alike.
# It stands for half the bid-ask spread plus the price impact of our own order. The levels follow
# Frazzini, Israel and Moskowitz (2018), who measured about 9 bp for large caps and 19 bp for small
# caps on the real trades of a large manager. They are assumptions, the same for all years.
COST_BPS = {"top 500": 10, "top 1000": 15, "top 3000": 20}

# Fee for borrowing the stocks we short, in % per year of the dollars held short. D'Avolio (2002)
# found that 91% of stocks cost under 1% per year to borrow (0.17% on average) and the other 9%,
# mostly small stocks, 4.3% on average. These levels are assumptions, the same for all years.
BORROW_FEE_PCT = {"top 500": 0.25, "top 1000": 0.5, "top 3000": 1.0}
VERSION_COLORS = {"raw": "#a3a29c", "neutral": "#1f1e1c"}


def in_sample(universe: pd.DataFrame) -> pd.DataFrame:
    """Keep the formation dates whose holding month (the next month) is in SAMPLE_START..SAMPLE_END.

    Every portfolio is formed from the universe, so this one cut sets the backtest period.
    """
    holding_month = universe["date"].dt.to_period("M") + 1
    return universe[(holding_month >= SAMPLE_START) & (holding_month <= SAMPLE_END)]


def run_backtest() -> tuple[dict[tuple[str, str, str], pd.DataFrame], pd.DataFrame, pd.DataFrame, dict]:
    """Run every (version, signal, universe) and return four things.

    portfolios: the decile portfolio returns of every (version, signal, universe). The combined
                portfolio is in it too, as a table with the single column "long_short".
    traded:     the dollars traded each month by every long-short portfolio and by the combined
                portfolio (one column per (version, signal, universe), see dollars_traded).
    shorted:    the dollars held short each month, same columns (see dollars_short).
    combined_weights: the holdings of the combined portfolio, one table per (version, universe)
                (rows: formation date, columns: ticker). capacity.py uses them.
    """
    universe = in_sample(build_universe(n_stocks=3000))
    monthly_prices = build_monthly_prices()
    monthly_returns = build_monthly_returns(monthly_prices)
    daily_returns = build_daily_returns(universe["ticker"].unique())
    factors = load_table("french_factors_daily")

    signals = {
        "momentum": momentum(monthly_prices),
        "short_term_reversal": short_term_reversal(monthly_prices),
        "low_volatility": low_volatility(daily_returns, factors),
        "post_earnings_drift": post_earnings_drift(monthly_prices.index),  # fades with age, see signals.py
    }
    sectors = load_sectors()
    betas = estimate_betas(daily_returns, factors, monthly_prices.index)

    portfolios, traded, shorted, combined_weights = {}, {}, {}, {}
    for universe_name, n_stocks in UNIVERSE_SIZES.items():
        # rank 1 is the largest stock, so rank <= 500 is the top 500 by market cap.
        sub_universe = universe[universe["rank"] <= n_stocks]
        weights = {}  # holdings of this universe's long-short portfolios, key (version, signal)
        for signal_name, signal in signals.items():
            # Neutralize inside the universe we trade: the regression uses only its stocks.
            neutral_signal = neutralize(signal, sub_universe, sectors, betas)
            for version, score in {"raw": signal, "neutral": neutral_signal}.items():
                key = (version, signal_name, universe_name)
                portfolios[key] = sort_into_portfolios(score, sub_universe, monthly_returns)
                weights[(version, signal_name)] = long_short_weights(score, sub_universe, monthly_returns)
                traded[key] = dollars_traded(weights[(version, signal_name)], monthly_returns)
                shorted[key] = dollars_short(weights[(version, signal_name)], monthly_returns)

        # The combined portfolio: the four portfolios netted against each other and scaled back
        # to $1 long / $1 short. A stock that one signal buys and another one sells is held, and
        # traded, only for the difference. It exists in the months where all four signals do.
        for version in ["raw", "neutral"]:
            holdings = combine_weights([weights[(version, name)] for name in COMBINED_SIGNALS])
            combined_weights[(version, universe_name)] = holdings
            combined_returns = portfolio_returns(holdings, monthly_returns)
            portfolios[(version, COMBINED, universe_name)] = pd.DataFrame({"long_short": combined_returns})
            traded[(version, COMBINED, universe_name)] = dollars_traded(holdings, monthly_returns)
            shorted[(version, COMBINED, universe_name)] = dollars_short(holdings, monthly_returns)
    return portfolios, pd.DataFrame(traded), pd.DataFrame(shorted), combined_weights


def build_summary(long_short: pd.DataFrame) -> pd.DataFrame:
    """Summary statistics of every long-short portfolio, plus its beta to the market."""
    market_returns = build_monthly_market_returns(load_table("french_factors_daily"))
    summary = pd.DataFrame({key: summarize(returns) for key, returns in long_short.items()}).T
    summary["market beta"] = [market_beta(returns, market_returns) for _, returns in long_short.items()]
    return summary


def build_cost_summary(long_short: pd.DataFrame, traded: pd.DataFrame, shorted: pd.DataFrame) -> pd.DataFrame:
    """Turnover, trading cost, borrowing fee, net return and break-even cost of every long-short portfolio.

    Each universe has its own trading cost, COST_BPS, and borrowing fee, BORROW_FEE_PCT.
    See summarize_costs().
    """
    rows = {}
    for key in long_short.columns:
        universe_name = key[2]
        rows[key] = summarize_costs(
            long_short[key], traded[key], shorted[key], COST_BPS[universe_name], BORROW_FEE_PCT[universe_name]
        )
    return pd.DataFrame(rows).T


def net_returns(long_short: pd.DataFrame, traded: pd.DataFrame, shorted: pd.DataFrame) -> pd.DataFrame:
    """Long-short returns after costs: gross return - trading cost - borrowing fee of the month."""
    columns = long_short.columns
    cost_per_dollar_traded = pd.Series({key: COST_BPS[key[2]] / 10000 for key in columns})
    fee_per_dollar_short = pd.Series({key: BORROW_FEE_PCT[key[2]] / 100 / 12 for key in columns})
    return long_short - traded[columns] * cost_per_dollar_traded - shorted[columns] * fee_per_dollar_short


def style_growth_axis(axis, universe_name: str) -> None:
    """Log scale in dollars, a line at $1, light grid: shared by the growth-of-$1 charts."""
    axis.axhline(1, color="#52514e", linewidth=0.8)
    axis.set_yscale("log")
    axis.set_yticks([0.25, 0.5, 1, 2, 4, 8, 16])
    axis.set_yticklabels(["$0.25", "$0.50", "$1", "$2", "$4", "$8", "$16"])
    axis.minorticks_off()
    axis.set_title(f"Universe: {universe_name} stocks by market cap", loc="left", fontsize=11)
    axis.grid(axis="y", color="#e4e3df", linewidth=0.8)
    axis.spines[["top", "right", "left"]].set_visible(False)
    axis.tick_params(length=0, labelsize=9)


def show_or_save(figure, path) -> None:
    """Save the chart to `path`, or show it (in a notebook) when `path` is None."""
    figure.tight_layout()
    if path is None:
        plt.show()
    else:
        figure.savefig(path, dpi=150, facecolor="#fcfcfb")
        plt.close(figure)


def plot_cumulative_returns(long_short: pd.DataFrame, version: str, path=None, period: str = "") -> None:
    """Plot the growth of $1 in each long-short portfolio of one version, one panel per universe.

    `long_short` has columns (signal, universe). `version` ("raw" or "neutral") goes in the title,
    and so does `period` (for example "last 10 years") when the rows are only part of the sample.
    With `path` the chart is saved to that file; without it the chart is shown (in a notebook).
    """
    figure, axes = plt.subplots(1, len(UNIVERSE_SIZES), figsize=(6 * len(UNIVERSE_SIZES), 5), sharey=True)
    for axis, universe_name in zip(axes, UNIVERSE_SIZES):
        for signal_name, color in SIGNAL_COLORS.items():
            returns = long_short[(signal_name, universe_name)].dropna()
            value = (1 + returns).cumprod()
            axis.plot(value.index, value, color=color, linewidth=2, label=signal_name.replace("_", " "))
        style_growth_axis(axis, universe_name)
    axes[0].legend(frameon=False, fontsize=9, loc="upper left")
    figure.suptitle(
        f"Growth of $1 in each long-short portfolio, {version} signals{', ' + period if period else ''} "
        "(top decile minus bottom decile, equal-weighted, before costs)",
        x=0.01, ha="left", fontsize=12,
    )
    show_or_save(figure, path)


def plot_combined_returns(long_short: pd.DataFrame, path=None, period: str = "") -> None:
    """Plot the growth of $1 in the combined portfolio, raw and neutral, one panel per universe.

    `long_short` has columns (version, signal, universe) and includes the combined portfolio.
    """
    figure, axes = plt.subplots(1, len(UNIVERSE_SIZES), figsize=(6 * len(UNIVERSE_SIZES), 5), sharey=True)
    for axis, universe_name in zip(axes, UNIVERSE_SIZES):
        for version, color in VERSION_COLORS.items():
            returns = long_short[(version, COMBINED, universe_name)].dropna()
            value = (1 + returns).cumprod()
            axis.plot(value.index, value, color=color, linewidth=2, label=f"{version} signals")
        style_growth_axis(axis, universe_name)
    axes[0].legend(frameon=False, fontsize=9, loc="upper left")
    figure.suptitle(
        f"Growth of $1 in the four signals combined{', ' + period if period else ''} "
        "(equal capital in each, netted and scaled to $1 long / $1 short, before costs)",
        x=0.01, ha="left", fontsize=12,
    )
    show_or_save(figure, path)


def plot_net_returns(long_short: pd.DataFrame, net: pd.DataFrame, path=None, period: str = "") -> None:
    """Plot the growth of $1 in the neutral combined portfolio before and after costs."""
    figure, axes = plt.subplots(1, len(UNIVERSE_SIZES), figsize=(6 * len(UNIVERSE_SIZES), 5), sharey=True)
    for axis, universe_name in zip(axes, UNIVERSE_SIZES):
        key = ("neutral", COMBINED, universe_name)
        lines = {"before costs": (long_short[key], "#a3a29c"),
                 f"after costs ({COST_BPS[universe_name]} bp per $ traded, "
                 f"{BORROW_FEE_PCT[universe_name]}% per year to borrow)": (net[key], "#1f1e1c")}
        for label, (returns, color) in lines.items():
            value = (1 + returns.dropna()).cumprod()
            axis.plot(value.index, value, color=color, linewidth=2, label=label)
        style_growth_axis(axis, universe_name)
        axis.legend(frameon=False, fontsize=9, loc="upper left")
    figure.suptitle(
        f"Growth of $1 in the four neutral signals combined, before and after trading costs and borrowing fees{', ' + period if period else ''}",
        x=0.01, ha="left", fontsize=12,
    )
    show_or_save(figure, path)


if __name__ == "__main__":
    RESULTS_DIR.mkdir(exist_ok=True)
    portfolios, traded, shorted, _ = run_backtest()

    long_short = pd.DataFrame({key: table["long_short"] for key, table in portfolios.items()})
    summary = build_summary(long_short)
    decile_means = pd.DataFrame(
        {
            key: 100 * table.drop(columns="long_short").mean()
            for key, table in portfolios.items()
            if key[1] != COMBINED  # the combined portfolio is not sorted into groups
        }
    ).T

    long_short.to_csv(RESULTS_DIR / "long_short_returns.csv")
    summary.to_csv(RESULTS_DIR / "summary.csv")
    decile_means.to_csv(RESULTS_DIR / "decile_mean_returns.csv")
    plot_cumulative_returns(long_short["raw"], "raw", RESULTS_DIR / "cumulative_returns.png")
    plot_cumulative_returns(long_short["neutral"], "neutral", RESULTS_DIR / "cumulative_returns_neutral.png")
    plot_combined_returns(long_short, RESULTS_DIR / "cumulative_returns_combined.png")

    # The same statistics and charts on the last 10 years alone. The portfolios are the same ones;
    # only the months that are counted change.
    recent = long_short.iloc[-RECENT_MONTHS:]
    recent_summary = build_summary(recent)
    recent_summary.to_csv(RESULTS_DIR / "summary_last_10_years.csv")
    plot_cumulative_returns(
        recent["neutral"], "neutral", RESULTS_DIR / "cumulative_returns_neutral_last_10_years.png", RECENT_PERIOD
    )
    plot_combined_returns(recent, RESULTS_DIR / "cumulative_returns_combined_last_10_years.png", RECENT_PERIOD)

    # Transaction costs: full sample and last 10 years.
    cost_summary = build_cost_summary(long_short, traded, shorted)
    recent_cost_summary = build_cost_summary(recent, traded.iloc[-RECENT_MONTHS:], shorted.iloc[-RECENT_MONTHS:])
    net = net_returns(long_short, traded, shorted)
    cost_summary.to_csv(RESULTS_DIR / "summary_costs.csv")
    recent_cost_summary.to_csv(RESULTS_DIR / "summary_costs_last_10_years.csv")
    net.to_csv(RESULTS_DIR / "long_short_returns_net.csv")
    plot_net_returns(long_short, net, RESULTS_DIR / "cumulative_returns_combined_net.png")

    pd.set_option("display.width", 200)
    print("Long-short portfolios (top decile minus bottom decile, equal-weighted, before costs):")
    print(summary.to_string(float_format=lambda x: f"{x:.2f}"))
    print(f"\nThe same long-short portfolios, {RECENT_PERIOD} only:")
    print(recent_summary.to_string(float_format=lambda x: f"{x:.2f}"))
    print(f"\nAfter trading costs of {COST_BPS} bp per dollar traded and borrowing fees of {BORROW_FEE_PCT} % per year:")
    print(cost_summary.to_string(float_format=lambda x: f"{x:.2f}"))
    print(f"\nAfter trading costs and borrowing fees, {RECENT_PERIOD} only:")
    print(recent_cost_summary.to_string(float_format=lambda x: f"{x:.2f}"))
    print("\nAverage monthly return of each group, % (1 = lowest score, 10 = highest):")
    print(decile_means.to_string(float_format=lambda x: f"{x:.2f}"))
    print(f"\nSaved to {RESULTS_DIR}")
