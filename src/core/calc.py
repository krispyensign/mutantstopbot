"""Functions for calculating trading signals."""

from typing import Any
import numpy as np
from numpy.typing import NDArray
from numba import jit  # type: ignore


@jit(nopython=True)  # type: ignore
def exit_total(
    position_value: NDArray[np.float64],
    trigger: NDArray[np.int64],
    signal: NDArray[np.int64],
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Calculate the cumulative total of all trades and the running total of the portfolio.

    Parameters
    ----------
    position_value : NDArray[Any]
        The position values.
    trigger : NDArray[Any]
        The trigger values.
    signal : NDArray[Any]
        The signal values.

    Returns
    -------
    tuple[NDArray[np.float64], NDArray[np.float64]]
        A tuple containing the 'exit_value', 'exit_total' and 'running_total' arrays.

    Notes
    -----
    The 'exit_total' array is the cumulative total of all trades, and the 'running_total' array
    is the cumulative total of the portfolio, including the current trade.

    """
    exit_value = np.where(trigger == -1, position_value, 0)
    exit_total = np.cumsum(exit_value)
    running_total = exit_total + position_value * signal
    return exit_value, exit_total, running_total


@jit(nopython=True)  # type: ignore
def take_profit(
    position_value: NDArray[Any],
    atr: NDArray[Any],
    signal: NDArray[Any],
    take_profit_value: float,
    trigger: NDArray[Any],
) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
    """Apply a take profit strategy to trading signals.

    Parameters
    ----------
    position_value : np.ndarray
        The array of position values.
    atr : np.ndarray
        The array of average true range (atr).
    signal : np.ndarray
        The array of trading signals.
    trigger : np.ndarray
        The array of trigger values.
    take_profit_value : float
        The take profit value as a multiplier of the atr.

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        A tuple containing the updated 'signal' and 'trigger' arrays.

    Notes
    -----
    The 'signal' array is set to 0 where the 'position_value' array is greater than the
    'atr' array times the take profit value. The 'trigger' array is set to the difference
    between the 'signal' array and the previous value of the 'signal' array.

    """
    take_profit_array = take_profit_value * atr
    signal = np.where((position_value > take_profit_array) & (trigger != 1), 0, signal)
    trigger = np.diff(signal)
    trigger = np.concatenate((np.zeros(1), trigger))
    return signal.astype(np.int64), trigger.astype(np.int64)


@jit(nopython=True)  # type: ignore
def stop_loss(
    position_value: NDArray[Any],
    atr: NDArray[Any],
    signal: NDArray[Any],
    stop_loss_value: float,
    trigger: NDArray[Any],
) -> tuple[NDArray[Any], NDArray[Any]]:
    """Apply a stop loss strategy to trading signals.

    This function takes arrays of position values, average true range (atr), signals,
    and a stop loss multiplier to determine when to stop out of trades. If the position
    value falls below the stop loss threshold, the signal is set to 0. The function
    calculates the trigger as the difference between consecutive signal values.

    Parameters
    ----------
    position_value : NDArray[Any]
        Numpy array containing the position values.
    atr : NDArray[Any]
        Numpy array containing the average true range values.
    signal : NDArray[Any]
        Numpy array containing the trading signals.
    trigger: NDArray[Any]
        Numpy array containing the triggers.
    stop_loss_value : float
        The stop loss value as a multiplier of the atr.

    Returns
    -------
    tuple[NDArray[Any], NDArray[Any]]
        A tuple containing the updated signal and the trigger arrays.

    """
    stop_loss_array = -stop_loss_value * atr
    signal = np.where(position_value < stop_loss_array, 0, signal)
    trigger = np.diff(signal)
    trigger = np.concatenate((np.zeros(1), trigger))
    return signal.astype(np.int64), trigger.astype(np.int64)


@jit(nopython=True)  # type: ignore
def forward_fill(arr: NDArray[Any]) -> NDArray[Any]:
    """Forward fills NaN values in a 1D NumPy array.

    Parameters
    ----------
    arr : np.ndarray
        A 1D NumPy array containing potentially NaN values.

    Returns
    -------
        np.ndarray: A new 1D NumPy array with NaN values forward filled.

    """
    last_valid = np.nan
    result = np.empty_like(arr)
    for i in range(arr.shape[0]):
        if not np.isnan(arr[i]):
            last_valid = arr[i]
        result[i] = last_valid
    return result


@jit(nopython=True)  # type: ignore
def entry_price(
    entry: NDArray[np.float64],
    exit: NDArray[np.float64],
    signal: NDArray[np.int64],
    trigger: NDArray[np.int64],
) -> NDArray[np.float64]:
    """Calculate the entry price for a given trading signal.

    Parameters
    ----------
    entry : np.ndarray
        The entry price array.
    exit : np.ndarray
        The exit price array.
    signal : np.ndarray
        The signal array.
    trigger : np.ndarray
        The trigger array.

    Returns
    -------
    tuple[np.ndarray, np.ndarray, np.ndarray]
        A tuple containing the internal bit mask, exit price, and position value arrays.

    """
    internal_bit_mask = np.logical_or(signal, trigger)
    entry_price = np.where(trigger == 1, entry, np.nan)
    entry_price = forward_fill(entry_price) * internal_bit_mask
    position_value = (exit - entry_price) * internal_bit_mask

    return position_value.astype(np.float64)  # type: ignore
