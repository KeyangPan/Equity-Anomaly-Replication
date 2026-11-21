"""How much money can the combined portfolio run? Net return as a function of fund size.

Usage:
    uv run python -m anomaly_replication.capacity

backtest.py charges the same cost for every dollar traded. Here the cost of an order depends
on the stock and on the size of the order, so it grows with the size of the fund:

    cost of an order, in bp of its value = half spread + price impact

    half spread   by the stock's size rank (HALF_SPREAD_BPS): what a small order pays.
    price impact  IMPACT_BPS * sqrt(order as a % of the stock's average daily dollar volume).
                  This is the square-root law estimated by Frazzini, Israel and Moskowitz (2018,
                  "Trading Costs", Table VII, U.S. sample) on $1.7 trillion of real AQR trades.

Writes to results/:
    capacity.csv   one row per (universe, fund size): cost, net return, Sharpe, order sizes
    capacity.png   net Sharpe ratio and cost per dollar traded against fund size
"""

import matplotlib

matplotlib.use("Agg")  # draw to a file, no window needed
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from anomaly_replication.backtest import (
    BORROW_FEE_PCT,
    COMBINED,
    RECENT_MONTHS,
    RESULTS_DIR,
    UNIVERSE_SIZES,
    run_backtest,
)
from anomaly_replication.liquidity import average_dollar_volume, fix_volume_errors
from anomaly_replication.portfolio import next_month_end, summarize, weight_changes
from anomaly_replication.returns import build_monthly_prices, build_monthly_returns
from anomaly_replication.universe import build_universe

# Fund sizes to test, in today's dollars. "Fund size" = dollars long = dollars short.
FUND_SIZES = [1e6, 3e6, 1e7, 3e7, 1e8, 3e8, 1e9, 3e9]

# Price impact in bp for an order of 1% of daily volume; it grows with the square root of the size.
IMPACT_BPS = 11.21

# Half the bid-ask spread in bp, by the stock's size rank: up to 500, up to 1000, the rest.
# Large caps trade about 1 bp from the mid price today. For small caps, 9 bp is what is left of
# the 19 bp that Frazzini, Israel and Moskowitz measured once the impact of their order size is
# taken out. These are assumptions, the same for all years.
HALF_SPREAD_BPS = {500: 1.0, 1000: 3.0, 3000: 9.0}

# Frazzini, Israel and Moskowitz: the average order was 0.9% of daily volume, the largest 13.1%.
AQR_ORDER_PCT = 0.9
AQR_LARGEST_ORDER_PCT = 13.1

UNIVERSE_COLORS = {"top 500": "#2a78d6", "top 1000": "#eb6834", "top 3000": "#1baf7a"}


def half_spreads(universe: pd.DataFrame) -> pd.DataFrame:
    """Return each stock's half spread in bp at each month-end (rows: date, columns: ticker).

    A stock outside the top 3000 that month (we may still be selling it) gets the widest spread.
    """
    rank = universe.pivot(index="date", columns="ticker", values="rank")
    spread = pd.DataFrame(max(HALF_SPREAD_BPS.values()), index=rank.index, columns=rank.columns)
    for max_rank in sorted(HALF_SPREAD_BPS, reverse=True):
        spread = spread.mask(rank <= max_rank, HALF_SPREAD_BPS[max_rank])
    return spread


def cost_at_fund_size(
    trades: pd.DataFrame, fund_size: pd.Series, spread_bps: pd.DataFrame, volume: pd.DataFrame
) -> pd.DataFrame:
    """Return the monthly trading cost of a portfolio run at a given size.

    trades:     trade in every stock per $1 of fund size (see weight_changes), rows = formation date.
    fund_size:  dollars of fund size at each formation date.
    spread_bps: half spread of each stock in bp.
    volume:     average daily dollar volume of each stock (after fix_volume_errors).

    For every order:  dollars  = fund size * |trade|
                      order %  = 100 * dollars / daily dollar volume
                      cost bp  = half spread + IMPACT_BPS * sqrt(order %)

    Output, one row per formation date, all per $1 of fund size:
        traded        dollars traded
        spread cost   dollars paid for the spread
        impact cost   dollars paid for price impact
        order %       average order size as % of daily volume, weighted by dollars traded
        large orders  share of the dollars traded in orders above AQR_LARGEST_ORDER_PCT
    """
    size = trades.abs()
    spread_bps = spread_bps.reindex(index=size.index, columns=size.columns).fillna(max(HALF_SPREAD_BPS.values()))
    volume = volume.reindex(index=size.index, columns=size.columns)

    order_pct = 100 * size.mul(fund_size, axis=0) / volume
    impact_bps = IMPACT_BPS * np.sqrt(order_pct)
    traded = size.sum(axis=1)
    return pd.DataFrame(
        {
            "traded": traded,
            "spread cost": (size * spread_bps).sum(axis=1) / 10000,
            "impact cost": (size * impact_bps).sum(axis=1) / 10000,
            "order %": (size * order_pct).sum(axis=1) / traded,
            "large orders": size.where(order_pct > AQR_LARGEST_ORDER_PCT, 0.0).sum(axis=1) / traded,
        }
    )


def run_capacity() -> pd.DataFrame:
    """Return one row per (universe, fund size) for the neutral combined portfolio."""
    portfolios, _, shorted, combined_weights = run_backtest()
    long_short = pd.DataFrame({key: table["long_short"] for key, table in portfolios.items()})

    universe = build_universe(n_stocks=3000)
    monthly_returns = build_monthly_returns(build_monthly_prices())
    volume = fix_volume_errors(average_dollar_volume(universe["ticker"].unique()), universe)
    spread_bps = half_spreads(universe)
    to_holding_month = next_month_end(monthly_returns)

    # A fund of a fixed dollar size was a much larger part of the market in 2000 than today.
    # So the fund size is given in today's dollars and scaled back in time with the total
    # dollar volume of the top 3000 stocks: the fund is always the same share of market trading.
    market_volume = universe.merge(volume.stack().rename("volume").reset_index(), on=["date", "ticker"])
    market_volume = market_volume.groupby("date")["volume"].sum()
    relative_volume = market_volume / market_volume.iloc[-12:].mean()

    rows = {}
    for universe_name in UNIVERSE_SIZES:
        key = ("neutral", COMBINED, universe_name)
        trades = weight_changes(combined_weights[("neutral", universe_name)], monthly_returns)
        gross = long_short[key].dropna()
        borrow_cost = shorted[key] * BORROW_FEE_PCT[universe_name] / 100 / 12

        for fund_size in FUND_SIZES:
            scaled_size = fund_size * relative_volume.reindex(trades.index)
            costs = cost_at_fund_size(trades, scaled_size, spread_bps, volume)
            costs.index = costs.index.map(to_holding_month)
            costs = costs.reindex(gross.index)

            trading_cost = costs["spread cost"] + costs["impact cost"]
            net = gross - trading_cost - borrow_cost.reindex(gross.index)
            net_summary, recent_summary = summarize(net), summarize(net.iloc[-RECENT_MONTHS:])
            traded = costs["traded"].sum()
            rows[(universe_name, fund_size)] = {
                "gross mean % per month": 100 * gross.mean(),
                "spread bp per $ traded": 10000 * costs["spread cost"].sum() / traded,
                "impact bp per $ traded": 10000 * costs["impact cost"].sum() / traded,
                "cost bp per $ traded": 10000 * trading_cost.sum() / traded,
                "trading cost % per month": 100 * trading_cost.mean(),
                "net mean % per month": net_summary["mean % per month"],
                "net t-stat": net_summary["t-stat"],
                "net NW t-stat": net_summary["NW t-stat"],
                "net annual Sharpe": net_summary["annual Sharpe"],
                "net annual Sharpe, last 10 years": recent_summary["annual Sharpe"],
                "average order % of daily volume": (costs["order %"] * costs["traded"]).sum() / traded,
                "share of $ in orders above 13.1%": (costs["large orders"] * costs["traded"]).sum() / traded,
            }
    capacity = pd.DataFrame(rows).T
    capacity.index.names = ["universe", "fund size $"]
    return capacity


def interpolate_fund_size(sizes: pd.Series, values: pd.Series, target: float) -> float:
    """Return the fund size at which `values` crosses `target`, interpolating on log(fund size).

    NaN when the values never cross the target on the grid.
    """
    for (size_a, value_a), (size_b, value_b) in zip(
        zip(sizes[:-1], values[:-1]), zip(sizes[1:], values[1:])
    ):
        if (value_a - target) * (value_b - target) <= 0 and value_a != value_b:
            share = (target - value_a) / (value_b - value_a)
            return float(np.exp(np.log(size_a) + share * (np.log(size_b) - np.log(size_a))))
    return float("nan")


def key_fund_sizes(capacity: pd.DataFrame) -> pd.DataFrame:
    """Return, per universe, the fund sizes that mark a threshold (in $ millions).

    AQR-like orders: the fund size at which our average order is AQR_ORDER_PCT of daily volume.
                     The average order is proportional to the fund size, so this is exact.
    break-even:      the fund size at which the average net return falls to zero.
    """
    rows = {}
    for universe_name in UNIVERSE_SIZES:
        table = capacity.loc[universe_name]
        sizes = table.index.to_numpy(dtype=float)
        order_pct_per_dollar = table["average order % of daily volume"].iloc[0] / sizes[0]
        rows[universe_name] = {
            "AQR-like orders, $M": AQR_ORDER_PCT / order_pct_per_dollar / 1e6,
            "break-even, $M": interpolate_fund_size(sizes, table["net mean % per month"].to_numpy(), 0.0) / 1e6,
        }
    return pd.DataFrame(rows).T


def format_dollars(value: float, _position=None) -> str:
    """$1M, $300M, $10B."""
    if value >= 1e9:
        return f"${value / 1e9:g}B"
    return f"${value / 1e6:g}M"


def plot_capacity(capacity: pd.DataFrame, path=None) -> None:
    """Plot the net Sharpe ratio and the cost per dollar traded against fund size, one line per universe.

    A dot on each line marks the fund size at which our average order is as large, relative to
    daily volume, as the average AQR order in Frazzini, Israel and Moskowitz (2018).
    """
    aqr_sizes = key_fund_sizes(capacity)["AQR-like orders, $M"] * 1e6
    panels = {
        "net annual Sharpe": "Net Sharpe ratio (after trading costs and borrowing fees)",
        "cost bp per $ traded": "Trading cost, bp per dollar traded (half spread + price impact)",
    }
    figure, axes = plt.subplots(1, 2, figsize=(13, 5))
    for axis, (column, title) in zip(axes, panels.items()):
        for universe_name, color in UNIVERSE_COLORS.items():
            values = capacity.loc[universe_name, column]
            sizes = values.index.to_numpy(dtype=float)
            axis.plot(sizes, values, color=color, linewidth=2, label=universe_name)
            # In the first panel, also name each line at its right end (in ink, not in the line's color).
            if column == "net annual Sharpe":
                axis.annotate(universe_name, (sizes[-1], values.iloc[-1]), xytext=(6, 0),
                              textcoords="offset points", va="center", fontsize=9, color="#1f1e1c")
            # The dot: AQR-like order size.
            aqr_size = aqr_sizes[universe_name]
            aqr_value = np.interp(np.log(aqr_size), np.log(sizes), values.to_numpy(dtype=float))
            axis.plot(aqr_size, aqr_value, "o", color=color, markersize=8,
                      markeredgecolor="#fcfcfb", markeredgewidth=2)
        axis.set_xscale("log")
        axis.set_xlim(FUND_SIZES[0], FUND_SIZES[-1] * 4)
        axis.set_xticks([1e6, 1e7, 1e8, 1e9])
        axis.xaxis.set_major_formatter(format_dollars)
        axis.minorticks_off()
        axis.set_xlabel("fund size in today's dollars (dollars long = dollars short)", fontsize=9)
        axis.set_title(title, loc="left", fontsize=11)
        axis.grid(axis="y", color="#e4e3df", linewidth=0.8)
        axis.set_axisbelow(True)
        axis.spines[["top", "right", "left"]].set_visible(False)
        axis.tick_params(length=0, labelsize=9)
    axes[0].axhline(0, color="#52514e", linewidth=0.8)
    axes[1].set_ylim(bottom=0)
    axes[1].legend(frameon=False, fontsize=9, loc="upper left")
    figure.suptitle(
        "The neutral combined portfolio at different fund sizes  "
        "(dot = orders as large as AQR's: 0.9% of daily volume on average)",
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
    capacity = run_capacity()
    capacity.to_csv(RESULTS_DIR / "capacity.csv")
    plot_capacity(capacity, RESULTS_DIR / "capacity.png")

    pd.set_option("display.width", 250)
    print("Neutral combined portfolio at different fund sizes:")
    print(capacity.to_string(float_format=lambda x: f"{x:.2f}"))
    print("\nFund sizes that mark a threshold, $ millions:")
    print(key_fund_sizes(capacity).to_string(float_format=lambda x: f"{x:,.0f}"))
    print(f"\nSaved to {RESULTS_DIR}")
