"""Backtest the trading strategy."""

from datetime import datetime, timedelta
import subprocess
from typing import Any
import numpy as np
import pandas as pd
import talib
import v20  # type: ignore
from numba import jit  # type: ignore
from bot.common import BacktestResult, ChartConfig, SolverConfig
from core.chart import heiken_ashi_numpy
from numpy.typing import NDArray

from core.kernel import EdgeCategory, KernelConfig, kernel_stage_1, kernel
from bot.exchange import (
    getOandaOHLC,
    OandaContext,
)

import logging


def get_git_info() -> tuple[str, bool] | Exception:
    """Get commit hash and whether the working tree is clean.

    Returns a tuple (str, bool). The first element is the commit hash. The second
    element is a boolean indicating whether the working tree is clean (i.e., there
    are no pending changes).

    If there is an error (e.g., not in a Git repository), the function returns None.
    """
    try:
        # Get commit hash
        commit_hash = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], encoding="utf-8"
        ).strip()

        # Get porcelain status
        porcelain_status = subprocess.check_output(
            ["git", "status", "--porcelain"], encoding="utf-8"
        ).strip()

        return commit_hash, porcelain_status == ""
    except subprocess.CalledProcessError as e:
        return e


def preprocess(df: pd.DataFrame, wma_period: int) -> pd.DataFrame:
    # calculate the ATR for the trailing stop loss
    """Preprocess the DataFrame to calculate various technical indicators.

    This function calculates the Average True Range (ATR), Weighted Moving Averages (WMA)
    for open, high, low, and close prices, and Heikin-Ashi candlesticks for both original
    and bid/ask prices in the given DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        A DataFrame containing columns for open, high, low, close, ask, and bid prices.
    wma_period : int
        The period to be used for calculating the Weighted Moving Averages (WMA).
    convert: bool
        If True, convert the DataFrame to a dictionary of NumPy arrays.

    Returns
    -------
    pd.DataFrame
        The input DataFrame with additional columns for ATR, WMA, and Heikin-Ashi
        candlesticks.

    """
    df["atr"] = talib.ATR(
        df["high"].to_numpy(),
        df["low"].to_numpy(),
        df["close"].to_numpy(),
        timeperiod=wma_period,
    )

    df["wma_open"] = talib.WMA(df["open"].to_numpy(), timeperiod=wma_period)
    df["wma_high"] = talib.WMA(df["high"].to_numpy(), timeperiod=wma_period)
    df["wma_low"] = talib.WMA(df["low"].to_numpy(), timeperiod=wma_period)
    df["wma_close"] = talib.WMA(df["close"].to_numpy(), timeperiod=wma_period)

    df["wma_ask_open"] = talib.WMA(df["ask_open"].to_numpy(), timeperiod=wma_period)
    df["wma_ask_high"] = talib.WMA(df["ask_high"].to_numpy(), timeperiod=wma_period)
    df["wma_ask_low"] = talib.WMA(df["ask_low"].to_numpy(), timeperiod=wma_period)
    df["wma_ask_close"] = talib.WMA(df["ask_close"].to_numpy(), timeperiod=wma_period)

    df["wma_bid_open"] = talib.WMA(df["bid_open"].to_numpy(), timeperiod=wma_period)
    df["wma_bid_high"] = talib.WMA(df["bid_high"].to_numpy(), timeperiod=wma_period)
    df["wma_bid_low"] = talib.WMA(df["bid_low"].to_numpy(), timeperiod=wma_period)
    df["wma_bid_close"] = talib.WMA(df["bid_close"].to_numpy(), timeperiod=wma_period)

    # calculate the Heikin-Ashi candlesticks
    df["ha_open"], df["ha_high"], df["ha_low"], df["ha_close"] = heiken_ashi_numpy(
        df["open"].to_numpy(),
        df["high"].to_numpy(),
        df["low"].to_numpy(),
        df["close"].to_numpy(),
    )
    df["wma_ha_open"] = talib.WMA(df["ha_open"].to_numpy(), timeperiod=wma_period)
    df["wma_ha_high"] = talib.WMA(df["ha_high"].to_numpy(), timeperiod=wma_period)
    df["wma_ha_low"] = talib.WMA(df["ha_low"].to_numpy(), timeperiod=wma_period)
    df["wma_ha_close"] = talib.WMA(df["ha_close"].to_numpy(), timeperiod=wma_period)

    # calculate the Heikin-Ashi candlesticks for the bid prices
    df["ha_bid_open"], df["ha_bid_high"], df["ha_bid_low"], df["ha_bid_close"] = (
        heiken_ashi_numpy(
            df["bid_open"].to_numpy(),
            df["bid_high"].to_numpy(),
            df["bid_low"].to_numpy(),
            df["bid_close"].to_numpy(),
        )
    )
    df["wma_ha_bid_open"] = talib.WMA(
        df["ha_bid_open"].to_numpy(), timeperiod=wma_period
    )
    df["wma_ha_bid_high"] = talib.WMA(
        df["ha_bid_high"].to_numpy(), timeperiod=wma_period
    )
    df["wma_ha_bid_low"] = talib.WMA(df["ha_bid_low"].to_numpy(), timeperiod=wma_period)
    df["wma_ha_bid_close"] = talib.WMA(
        df["ha_bid_close"].to_numpy(), timeperiod=wma_period
    )

    # calculate the Heikin-Ashi candlesticks for the ask prices
    df["ha_ask_open"], df["ha_ask_high"], df["ha_ask_low"], df["ha_ask_close"] = (
        heiken_ashi_numpy(
            df["ask_open"].to_numpy(),
            df["ask_high"].to_numpy(),
            df["ask_low"].to_numpy(),
            df["ask_close"].to_numpy(),
        )
    )
    df["wma_ha_ask_open"] = talib.WMA(
        df["ha_ask_open"].to_numpy(), timeperiod=wma_period
    )
    df["wma_ha_ask_high"] = talib.WMA(
        df["ha_ask_high"].to_numpy(), timeperiod=wma_period
    )
    df["wma_ha_ask_low"] = talib.WMA(df["ha_ask_low"].to_numpy(), timeperiod=wma_period)
    df["wma_ha_ask_close"] = talib.WMA(
        df["ha_ask_close"].to_numpy(), timeperiod=wma_period
    )

    return df


def _convert_to_dict(df: pd.DataFrame) -> dict[str, NDArray[np.float64]]:
    result_dict: dict[str, NDArray[np.float64]] = {}
    for k, v in df.to_dict("series").items():
        result_dict[str(k)] = v.to_numpy()
    return result_dict


def _solve_run(
    kernel_conf: KernelConfig,
    df: dict[str, NDArray[Any]],
    ask_data: NDArray[Any],
    atr: NDArray[Any],
) -> tuple[np.float64, np.float64, np.int64, np.int64, np.float64] | None:
    # run the backtest
    should_roll = (
        "open" not in kernel_conf.source_column
        and kernel_conf.edge != EdgeCategory.Deterministic
    )
    wma = df[f"wma_{kernel_conf.source_column}"]
    (
        _,
        _,
        _,
        exit_value,
        exit_total,
        _,
    ) = kernel_stage_1(
        df[kernel_conf.signal_buy_column],
        df[kernel_conf.signal_exit_column],
        wma,
        ask_data,
        df[kernel_conf.bid_column],
        atr,
        kernel_conf.take_profit,
        kernel_conf.stop_loss,
        kernel_conf.signal_buy_column != kernel_conf.signal_exit_column,
        should_roll,
        kernel_conf.edge == EdgeCategory.Quasi,
    )

    result = _stats(exit_value, exit_total)

    return result  # type: ignore


@jit(nopython=True)  # type: ignore
def _stats(
    exit_value: NDArray[Any], exit_total: NDArray[Any]
) -> tuple[np.float64, np.float64, np.int64, np.int64, np.float64] | None:
    final_total = exit_total[-1] if exit_total[-1] > 0 else np.float64(0.0)
    min_total = exit_total.min()
    max_total = exit_total.max()
    if final_total <= 0.0 or max_total < abs(min_total):
        return None

    wins: np.int64 = np.where(exit_value > 0, 1, 0).astype(np.int64).sum()
    losses: np.int64 = np.where(exit_value < 0, 1, 0).astype(np.int64).sum()
    ratio = (
        np.float64(wins / (wins + losses)) if (wins + losses) > 0 else np.float64(0.0)
    )

    return final_total, min_total, wins, losses, ratio


def solve(
    chart_config: ChartConfig,
    kernel_conf_in: KernelConfig,
    token: str,
    backtest_config: SolverConfig,
) -> BacktestResult | None:
    """Run a backtest of the trading strategy.

    Parameters
    ----------
    chart_config : ChartConfig
        The chart configuration.
    kernel_conf_in : KernelConfig
        The kernel configuration.
    token : str
        The Oanda API token.
    backtest_config : BacktestConfig
        The backtest configuration.

    Returns
    -------
    KernelConfig | None
        The best kernel configuration.

    Notes
    -----
    The backtest will run for a large number of combinations of source and signal
    columns. The best combination will be saved to best_df and the results will be
    printed to the log file.

    """
    logger = logging.getLogger("solve")
    logger.info("starting backtest")
    git_info = get_git_info()
    if type(git_info) is not tuple:
        logger.error("failed to get git info: %s", git_info)
        return None
    logger.info("git info: %s %s", git_info[0], git_info[1])

    # get data and preprocess
    orig_df = preprocess(
        _get_data(chart_config, token, logger), kernel_conf_in.wma_period
    )

    # convert to dict for speed
    df = _convert_to_dict(orig_df)
    best_result = _find_max(
        df,
        logger,
        backtest_config,
        chart_config,
        kernel_conf_in,
    )

    return best_result


def segmented_solve(
    chart_config: ChartConfig,
    kernel_conf_in: KernelConfig,
    token: str,
    solver_config: SolverConfig,
) -> tuple[float, float, float]:
    """Run a backtest of the trading strategy."""
    logger = logging.getLogger("backtest")
    logger.info("starting backtest")
    git_info = get_git_info()
    if type(git_info) is not tuple:
        logger.error("failed to get git info: %s", git_info)
        return 0.0, 0.0, 0.0
    logger.info("git info: %s %s", git_info[0], git_info[1])

    # get data and preprocess
    orig_df = preprocess(
        _get_data(chart_config, token, logger), kernel_conf_in.wma_period
    )

    orig_df_train = orig_df.head(solver_config.train_size)
    orig_df_tp_train = orig_df_train.tail(solver_config.sample_size)
    orig_df_sample = orig_df.tail(solver_config.sample_size)
    df_train = _convert_to_dict(orig_df_train)
    df_tp_train = _convert_to_dict(orig_df_tp_train)
    df_sample = _convert_to_dict(orig_df_sample)

    raw_zk_result = _find_max(
        df_train,
        logger,
        solver_config,
        chart_config,
        kernel_conf_in,
    )
    refined_result_zk: BacktestResult | None = None
    if raw_zk_result is None:
        logger.error("failed to find best result")
        return 0.0, 0.0, 0.0
    df = kernel(orig_df_sample.copy(), raw_zk_result.kernel_conf)
    raw_zk_bet = df.iloc[-1].exit_total
    if np.isnan(raw_zk_bet):
        raw_zk_bet = 0.0

    logger.info("best result: %s", raw_zk_result)
    refined_result_zk = _find_max(
        df_tp_train,
        logger,
        solver_config,
        chart_config,
        raw_zk_result.kernel_conf,
    )
    if refined_result_zk is not None:
        logger.info("refined zero knowledge result: %s", refined_result_zk)
        df = kernel(orig_df_sample.copy(), refined_result_zk.kernel_conf)
        zk_bet = df.iloc[-1].exit_total
    else:
        logger.error("failed to find refined zero knowledge result")
        zk_bet = 0.0
        if np.isnan(zk_bet):
            zk_bet = 0.0

    result_pk = _find_max(
        df_sample,
        logger,
        solver_config,
        chart_config,
        raw_zk_result.kernel_conf,
    )
    if result_pk is not None:
        logger.info("perfect knowledge result: %s", result_pk)
        df = kernel(orig_df_sample.copy(), result_pk.kernel_conf)
        pk_bet = df.iloc[-1].exit_total
        if np.isnan(pk_bet):
            pk_bet = 0.0
    else:
        logger.error("failed to find perfect knowledge result")
        pk_bet = 0.0

    logger.info(
        "raw_zk_bet: %s refined_zk_bet: %s pk_bet: %s",
        round(raw_zk_bet, 5),
        round(zk_bet, 5),
        round(pk_bet, 5),
    )

    return raw_zk_bet, zk_bet, pk_bet


def _find_max(
    df: dict[str, NDArray[Any]],
    logger: logging.Logger,
    solver_config: SolverConfig,
    chart_config: ChartConfig,
    kernel_conf_in: KernelConfig,
) -> BacktestResult | None:
    # init
    best_result: BacktestResult | None = None
    total_found = 0
    count = 0
    filter_start_time = datetime.now()
    atr = df["atr"]
    ask = df["ask_close"]

    # run all combinations
    configs, num_configs = solver_config.get_configs(kernel_conf_in)
    logger.info(f"total_combinations: {num_configs}")
    for kernel_conf in configs:
        # log progress
        count = _log_progress(
            logger, num_configs, total_found, count, filter_start_time
        )

        # filter if needed
        if (
            solver_config.force_edge != ""
            and EdgeCategory[solver_config.force_edge] != kernel_conf.edge
        ):
            continue

        # run
        result = _solve_run(kernel_conf, df, ask, atr)
        if result is None:
            continue

        # update best
        total_found += 1
        et, _, wins, losses, ratio = result
        if (
            best_result is None
            or (ratio >= best_result.ratio)
            and (et >= best_result.exit_total)
        ):
            best_result = BacktestResult(
                chart_config.instrument,
                kernel_conf,
                et,
                np.float64(ratio),
                wins,
                losses,
            )
            logger.debug(best_result)

    logger.info("total_found: %s", total_found)
    if total_found == 0:
        logger.error("no combinations found")
        return None

    return best_result


def _log_progress(
    logger: logging.Logger,
    column_pair_len: int,
    total_found: int,
    count: int,
    start_time: datetime,
) -> int:
    if count == 0:
        logger.info("starting pass")
    count += 1
    if count % 10000 == 0:
        time_now = datetime.now()
        time_diff = time_now - start_time
        throughput = count / time_diff.total_seconds()
        remaining = timedelta(seconds=(column_pair_len - count) / throughput)
        logger.debug(
            "heartbeat: %s %s%% %s/%s %s/s %s remaining",
            total_found,
            round(100 * count / column_pair_len, 2),
            count,
            column_pair_len,
            round(throughput, 2),
            remaining,
        )
    return count


def _get_data(
    chart_config: ChartConfig,
    token: str,
    logger: logging.Logger,
    instrument: str | None = None,
) -> pd.DataFrame:
    """Get data from Oanda and return it as a DataFrame.

    Parameters
    ----------
    chart_config : ChartConfig
        The configuration for the chart.
    token : str
        The Oanda API token.
    logger : logging.Logger
        The logger to use.
    instrument : str | None, optional
        The instrument to get data for. The default is None, which means to use the instrument from the chart config.

    Returns
    -------
    pd.DataFrame
        The DataFrame containing the data.

    """
    ctx = OandaContext(
        v20.Context("api-fxpractice.oanda.com", token=token),
        None,
        token,
        chart_config.instrument if instrument is None else instrument,
    )

    orig_df = getOandaOHLC(
        ctx,
        count=chart_config.candle_count,
        granularity=chart_config.granularity,
        fromTime=chart_config.date_from,
    )
    logger.info(
        "count: %s granularity: %s",
        chart_config.candle_count,
        chart_config.granularity,
    )

    return orig_df
