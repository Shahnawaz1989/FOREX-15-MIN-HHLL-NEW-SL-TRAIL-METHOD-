import math
from typing import Dict


class StrategyCalculator:
    """
    Dual-side BO setups: Primary T1.5 (÷4), OPP T1 (÷5)
    """

    @staticmethod
    def get_pip_value_per_lot(pair: str, current_price: float) -> float:
        pair = pair.upper().replace('/', '').replace('_', '')
        if pair.endswith('USD'):
            return 10.0
        elif pair.endswith('JPY'):
            return (0.01 / current_price) * 100000
        elif pair.startswith('USD'):
            return (0.0001 / current_price) * 100000
        return 10.0

    @staticmethod
    def roundoff_pips(pips: float) -> int:
        """Floor: 4.9→4, 4.1→4"""
        return int(math.floor(pips))

    @staticmethod
    def calculate_t15(t1: float, t2: float) -> float:
        return (t1 + t2) / 2

    @staticmethod
    def calculate_sl_sizing_t15(t15: float, t1: float) -> int:
        """Primary BO: |T1.5-T1| / 5 → pips"""
        diff = abs(t15 - t1)
        pips = (diff / 5) / 0.0001
        return StrategyCalculator.roundoff_pips(pips)

    @staticmethod
    def calculate_sl_sizing_t1(t1: float, at: float) -> int:
        """OPP leg: |T1-AT| / 6 → pips"""
        diff = abs(t1 - at)
        pips = (diff / 6) / 0.0001
        return StrategyCalculator.roundoff_pips(pips)

    @staticmethod
    def calculate_tp_pips(sl_pips: int) -> int:
        return sl_pips * 2  # 1:2 RR

    @staticmethod
    def calculate_lot_size(fund: float, risk_percent: float, sl_pips: int,
                           pair: str, entry: float) -> float:
        if fund <= 0 or sl_pips <= 0:
            return 0.01
        risk_amount = fund * (risk_percent / 100)
        pip_value = StrategyCalculator.get_pip_value_per_lot(pair, entry)
        lot = risk_amount / (sl_pips * pip_value)
        # No upper cap
        return max(0.01, round(lot, 2))

    # ========== BUY BO ==========

    @staticmethod
    def get_buy_bo_primary(gann_levels: Dict, fund: float, risk_percent: float,
                           pair: str) -> Dict:
        """BUY BO Primary: T1.5 ÷4"""
        buy_t1 = gann_levels['buy_targets'][0]
        buy_t2 = gann_levels['buy_targets'][1]
        sell_t1 = gann_levels['sell_targets'][0]  # SL

        entry = StrategyCalculator.calculate_t15(buy_t1, buy_t2)
        sl_pips = StrategyCalculator.calculate_sl_sizing_t15(entry, buy_t1)
        tp_pips = StrategyCalculator.calculate_tp_pips(sl_pips)

        sl_level = sell_t1  # Gann SL
        tp_level = entry + tp_pips * 0.0001

        lot = StrategyCalculator.calculate_lot_size(
            fund, risk_percent, sl_pips, pair, entry)

        return {
            'side': 'BUY',
            'entry': round(entry, 5),
            'sl': round(sl_level, 5),
            'tp': round(tp_level, 5),
            'sl_pips': sl_pips,
            'tp_pips': tp_pips,
            'lot_size': lot,
        }

    @staticmethod
    def get_buy_bo_opp_sell(gann_levels: Dict, fund: float, risk_percent: float,
                            pair: str) -> Dict:
        """BUY BO OPP SELL: T1 ÷5"""
        sell_t1 = gann_levels['sell_targets'][0]
        buy_t1 = gann_levels['buy_targets'][0]  # SL

        entry = sell_t1
        sl_pips = StrategyCalculator.calculate_sl_sizing_t1(
            sell_t1, gann_levels['sell_at'])
        tp_pips = StrategyCalculator.calculate_tp_pips(sl_pips)

        sl_level = buy_t1  # Gann SL
        tp_level = entry - tp_pips * 0.0001

        lot = StrategyCalculator.calculate_lot_size(
            fund, risk_percent, sl_pips, pair, entry)

        return {
            'side': 'SELL',
            'entry': round(entry, 5),
            'sl': round(sl_level, 5),
            'tp': round(tp_level, 5),
            'sl_pips': sl_pips,
            'tp_pips': tp_pips,
            'lot_size': lot,
        }

    # ========== SELL BO ==========

    @staticmethod
    def get_sell_bo_primary(gann_levels: Dict, fund: float, risk_percent: float,
                            pair: str) -> Dict:
        """SELL BO Primary: T1.5 ÷4"""
        sell_t1 = gann_levels['sell_targets'][0]
        sell_t2 = gann_levels['sell_targets'][1]
        buy_t1 = gann_levels['buy_targets'][0]  # SL

        entry = StrategyCalculator.calculate_t15(sell_t1, sell_t2)
        sl_pips = StrategyCalculator.calculate_sl_sizing_t15(entry, sell_t1)
        tp_pips = StrategyCalculator.calculate_tp_pips(sl_pips)

        sl_level = buy_t1  # Gann SL
        tp_level = entry - tp_pips * 0.0001

        lot = StrategyCalculator.calculate_lot_size(
            fund, risk_percent, sl_pips, pair, entry)

        return {
            'side': 'SELL',
            'entry': round(entry, 5),
            'sl': round(sl_level, 5),
            'tp': round(tp_level, 5),
            'sl_pips': sl_pips,
            'tp_pips': tp_pips,
            'lot_size': lot,
        }

    @staticmethod
    def get_sell_bo_opp_buy(gann_levels: Dict, fund: float, risk_percent: float,
                            pair: str) -> Dict:
        """SELL BO OPP BUY: T1 ÷5"""
        buy_t1 = gann_levels['buy_targets'][0]
        sell_t1 = gann_levels['sell_targets'][0]  # SL

        entry = buy_t1
        sl_pips = StrategyCalculator.calculate_sl_sizing_t1(
            buy_t1, gann_levels['buy_at'])
        tp_pips = StrategyCalculator.calculate_tp_pips(sl_pips)

        sl_level = sell_t1  # Gann SL
        tp_level = entry + tp_pips * 0.0001

        lot = StrategyCalculator.calculate_lot_size(
            fund, risk_percent, sl_pips, pair, entry)

        return {
            'side': 'BUY',
            'entry': round(entry, 5),
            'sl': round(sl_level, 5),
            'tp': round(tp_level, 5),
            'sl_pips': sl_pips,
            'tp_pips': tp_pips,
            'lot_size': lot,
        }


# Test with your gann_levels
if __name__ == "__main__":
    gann_levels = {
        'buy_at': 1.7045,
        'buy_targets': [1.7077, 1.7117],
        'sell_at': 1.7004,
        'sell_targets': [1.6973, 1.6933],
    }
    fund, risk, pair = 100, 8, 'EURUSD'

    print("=== BUY BO ===")
    print("PRIMARY BUY:", StrategyCalculator.get_buy_bo_primary(
        gann_levels, fund, risk, pair))
    print("OPP SELL:  ", StrategyCalculator.get_buy_bo_opp_sell(
        gann_levels, fund, risk, pair))

    print("\n=== SELL BO ===")
    print("PRIMARY SELL:", StrategyCalculator.get_sell_bo_primary(
        gann_levels, fund, risk, pair))
    print("OPP BUY:    ", StrategyCalculator.get_sell_bo_opp_buy(
        gann_levels, fund, risk, pair))
