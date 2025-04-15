"""Bot that trades on Oanda."""

from datetime import datetime
import logging
from time import sleep
import pandas as pd
import v20  # type: ignore

from backtest import SignalConfig, backtest
from core.config import (
    BACKTEST_COUNT,
    GRANULARITY,
    REFRESH_RATE,
    TAKE_PROFIT_MULTIPLIER,
    WMA_PERIOD,
)
from core.kernel import kernel
from reporting import report
from exchange import (
    close_order,
    get_open_trade,
    getOandaOHLC,
    place_order,
    OandaContext,
)

logger = logging.getLogger("bot")
APP_START_TIME = datetime.now()


class Record:
    """Record class."""

    ATR: float
    take_profit: float
    wma: float
    signal: int
    trigger: int

    def __init__(self, df: pd.DataFrame):
        """Initialize a Record object."""
        self.ATR = df["atr"].iloc[-1]
        self.take_profit = (
            df["entry_price"].iloc[-1] + df["atr"].iloc[-1] * TAKE_PROFIT_MULTIPLIER
        )
        self.wma = df["wma"].iloc[-1]
        self.signal = df["signal"].iloc[-1]
        self.trigger = df["trigger"].iloc[-1]


class PerfTimer:
    """PerfTimer class."""

    def __init__(self, app_start_time: datetime):
        """Initialize a PerfTimer object."""
        self.app_start_time = app_start_time
        pass

    def __enter__(self):
        """Start the timer."""
        self.start = datetime.now()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Stop the timer."""
        self.end = datetime.now()
        logger.info(f"run interval: {self.end - self.start}")
        logger.info("up time: %s", (self.end - self.app_start_time))
        logger.info("last run time: %s", self.end.strftime("%Y-%m-%d %H:%M:%S"))


def bot_run(
    ctx: OandaContext, signal_conf: SignalConfig, instrument: str, amount: float
) -> tuple[int, Exception | None]:
    """Run the bot."""
    try:
        trade_id = get_open_trade(ctx)
        df = getOandaOHLC(
            ctx, instrument, count=BACKTEST_COUNT, granularity=GRANULARITY
        )
    except Exception as err:
        return -1, err

    kernel(
        df,
        wma_period=WMA_PERIOD,
        signal_buy_column=signal_conf.signal_buy_column,
        source_column=signal_conf.source_column,
    )
    rec = Record(df)

    if rec.trigger == 1 and trade_id == -1:
        try:
            trade_id = place_order(
                ctx,
                instrument,
                amount,
                take_profit=rec.take_profit,
                trailing_distance=rec.ATR,
                stop_loss=rec.wma,
            )

        except Exception as err:
            return -1, err

    if rec.trigger == -1 and trade_id != -1:
        try:
            close_order(ctx, trade_id)
        except Exception as err:
            return trade_id, err

    if rec.trigger == 0 and rec.signal == 0 and trade_id != -1:
        close_order(ctx, trade_id)
        report(df, signal_conf.signal_buy_column)
        assert trade_id == -1, "trades should not be open"

    # print the results
    report(df, signal_conf.signal_buy_column)

    return trade_id, None


def bot(token: str, account_id: str, instrument: str, amount: float) -> None:
    """Bot that trades on Oanda.

    This function trades on Oanda using the Oanda API. It places market orders based on the
    trading signals generated by the kernel function.  It closes the trade when the trigger is -1.

    Parameters
    ----------
    token : str
        The Oanda API token.
    account_id : str
        The Oanda account ID.
    instrument : str
        The instrument to trade.
    amount : float | None
        The amount to trade. If None, the bot will calculate the amount based
        on the current balance.

    """
    signal_conf = backtest(
        instrument=instrument,
        token=token,
    )
    logger.info("starting bot.")

    ctx = OandaContext(
        v20.Context("api-fxpractice.oanda.com", token=token), account_id, token
    )

    while True:
        with PerfTimer(APP_START_TIME):
            trade_id, err = bot_run(ctx, signal_conf, instrument, amount)
            if err is not None:
                logger.error(err)
                sleep(5)
                continue

            logger.info(f"columns used: {signal_conf}")
            logger.info(f"trade id: {trade_id}") if trade_id == -1 else None

        sleep(REFRESH_RATE)
