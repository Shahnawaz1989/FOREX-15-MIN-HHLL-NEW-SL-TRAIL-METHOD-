# order_mt5.py
import MetaTrader5 as mt5


def init_mt5():
    if not mt5.initialize():
        raise RuntimeError("MT5 init failed")


def shutdown_mt5():
    mt5.shutdown()


def place_market_order(symbol: str, side: str, lot: float, sl: float, tp: float):
    """
    side: 'B' (buy) ya 'S' (sell)
    """
    info = mt5.symbol_info_tick(symbol)
    if info is None:
        print("No tick data for", symbol)
        return None

    price = info.ask if side == "B" else info.bid
    order_type = mt5.ORDER_TYPE_BUY if side == "B" else mt5.ORDER_TYPE_SELL

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": 123456,
        "comment": "1H_ORB_BOT",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }

    result = mt5.order_send(request)
    print("order_send result:", result)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print("  -> ORDER FAILED:", result.comment)
        return None

    print("  -> ORDER OK, ticket:", result.order)
    return result.order
