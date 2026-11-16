"""Run the long-short backtest of every signal on both universes and save the results.

Usage:
    uv run python -m anomaly_replication.backtest

Writes to results/:
    summary.csv               one row per (signal, universe): mean, t-stat, Sharpe, max drawdown
    long_short_returns.csv    monthly long-short return of every (signal, universe)
    decile_mean_returns.csv   average monthly return of each of the 10 groups
    cumulative_returns.png    growth of $1 in each long-short portfolio

All returns are before transaction costs.
"""

import matplotlib

matplotlib.use("Agg")  # draw to a file, no window needed
import matplotlib.pyplot as plt
import pandas as pd

from anomaly_replication.loader import PROJECT_DIR, load_table
from anomaly_replication.portfolio import sort_into_portfolios, summarize
from anomaly_replication.returns import build_daily_returns, build_monthly_prices, build_monthly_returns
from anomaly_replication.signals import low_volatility, momentum, post_earnings_drift, short_term_reversal
from anomaly_replication.universe import build_universe

RESULTS_DIR = PROJECT_DIR / "results"

# Two universes: the largest 500 stocks (large caps, similar to the S&P 500) and the largest 3000.
UNIVERSE_SIZES = {"top 500": 500, "top 3000": 3000}

# One fixed color per signal, used in every chart.
SIGNAL_COLORS = {
    "momentum": "#2a78d6",
    "short_term_reversal": "#eb6834",
    "low_volatility": "#1baf7a",
    "post_earnings_drift": "#eda100",
}


def build_signals(universe: pd.DataFrame, monthly_prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Compute the score table of every signal."""
    daily_returns = build_daily_returns(universe["ticker"].unique())
    factors = load_table("french_factors_daily")
    return {
        "momentum": momentum(monthly_prices),
        "short_term_reversal": short_term_reversal(monthly_prices),
        "low_volatility": low_volatility(daily_returns, factors),
        "post_earnings_drift": post_earnings_drift(monthly_prices.index),
    }


def run_backtest() -> dict[tuple[str, str], pd.DataFrame]:
    """Return the decile portfolio returns of every (signal, universe) pair."""
    universe = build_universe(n_stocks=3000)
    monthly_prices = build_monthly_prices()
    monthly_returns = build_monthly_returns(monthly_prices)
    signals = build_signals(universe, monthly_prices)

    portfolios = {}
    for signal_name, signal in signals.items():
        for universe_name, n_stocks in UNIVERSE_SIZES.items():
            # rank 1 is the largest stock, so rank <= 500 is the top 500 by market cap.
            sub_universe = universe[universe["rank"] <= n_stocks]
            portfolios[(signal_name, universe_name)] = sort_into_portfolios(signal, sub_universe, monthly_returns)
    return portfolios


def plot_cumulative_returns(long_short: pd.DataFrame, path=None) -> None:
    """Plot the growth of $1 in each long-short portfolio, one panel per universe.

    With `path` the chart is saved to that file; without it the chart is shown (in a notebook).
    """
    figure, axes = plt.subplots(1, len(UNIVERSE_SIZES), figsize=(13, 5), sharey=True)
    for axis, universe_name in zip(axes, UNIVERSE_SIZES):
        for signal_name, color in SIGNAL_COLORS.items():
            returns = long_short[(signal_name, universe_name)].dropna()
            value = (1 + returns).cumprod()
            axis.plot(value.index, value, color=color, linewidth=2, label=signal_name.replace("_", " "))
        axis.axhline(1, color="#52514e", linewidth=0.8)
        axis.set_yscale("log")
        axis.set_yticks([0.25, 0.5, 1, 2, 4, 8, 16])
        axis.set_yticklabels(["$0.25", "$0.50", "$1", "$2", "$4", "$8", "$16"])
        axis.minorticks_off()
        axis.set_title(f"Universe: {universe_name} stocks by market cap", loc="left", fontsize=11)
        axis.grid(axis="y", color="#e4e3df", linewidth=0.8)
        axis.spines[["top", "right", "left"]].set_visible(False)
        axis.tick_params(length=0, labelsize=9)
    axes[0].legend(frameon=False, fontsize=9, loc="upper left")
    figure.suptitle(
        "Growth of $1 in each long-short portfolio (top decile minus bottom decile, equal-weighted, before costs)",
        x=0.01, ha="left", fontsize=12,
    )
    figure.tight_layout()
    if path is None:
        plt.show()
    else:
        figure.savefig(path, dpi=150, facecolor="#fcfcfb")
        plt.close(figure)


if __name__ == "__main__":
    RESULTS_DIR.mkdir(exist_ok=True)
    portfolios = run_backtest()

    long_short = pd.DataFrame({key: table["long_short"] for key, table in portfolios.items()})
    summary = pd.DataFrame({key: summarize(returns) for key, returns in long_short.items()}).T
    decile_means = pd.DataFrame({key: 100 * table.mean() for key, table in portfolios.items()}).T

    long_short.to_csv(RESULTS_DIR / "long_short_returns.csv")
    summary.to_csv(RESULTS_DIR / "summary.csv")
    decile_means.to_csv(RESULTS_DIR / "decile_mean_returns.csv")
    plot_cumulative_returns(long_short, RESULTS_DIR / "cumulative_returns.png")

    pd.set_option("display.width", 200)
    print("Long-short portfolios (top decile minus bottom decile, equal-weighted, before costs):")
    print(summary.to_string(float_format=lambda x: f"{x:.2f}"))
    print("\nAverage monthly return of each group, % (1 = lowest score, 10 = highest):")
    print(decile_means.to_string(float_format=lambda x: f"{x:.2f}"))
    print(f"\nSaved to {RESULTS_DIR}")
