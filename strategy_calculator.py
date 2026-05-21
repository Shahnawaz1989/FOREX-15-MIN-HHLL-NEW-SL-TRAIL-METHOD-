from typing import Dict


class StrategyCalculator:
    """
    Gann strategy calculator with flexible entry/target selection.

    STOPLOSS:
      - Entry BUY_AT   -> SL = SELL_AT
      - Entry BUY_T1   -> SL = BUY_AT
      - Entry SELL_AT  -> SL = BUY_AT
      - Entry SELL_T1  -> SL = SELL_AT

    DEFAULT TARGET:
      - Entry BUY_AT   -> Target = BUY_T1
      - Entry BUY_T1   -> Target = BUY_T15
      - Entry SELL_AT  -> Target = SELL_T1
      - Entry SELL_T1  -> Target = SELL_T15
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
    def _pip_multiplier(pair: str) -> float:
        pair = pair.upper().replace('/', '').replace('_', '')
        if pair.endswith("JPY"):
            return 100.0
        return 10000.0

    @staticmethod
    def _calc_sl_pips(entry: float, sl: float, pair: str) -> float:
        mult = StrategyCalculator._pip_multiplier(pair)
        return abs(entry - sl) * mult

    @staticmethod
    def calculate_lot_size(
        fund: float,
        risk_percent: float,
        sl_pips: float,
        pair: str,
        entry: float,
    ) -> float:
        if fund <= 0 or sl_pips <= 0:
            return 0.01

        risk_amount = fund * (float(risk_percent) / 100.0)
        pip_value = StrategyCalculator.get_pip_value_per_lot(pair, entry)
        per_lot_risk = sl_pips * pip_value

        if per_lot_risk <= 0:
            return 0.01

        lot = risk_amount / per_lot_risk
        lot = max(0.01, round(lot, 2))
        return lot

    @staticmethod
    def _extract_levels(gann: Dict) -> Dict:
        buy_t1 = float(gann["buy_t1"]) if "buy_t1" in gann else float(
            gann["buy_targets"][0])
        buy_t2 = float(gann["buy_t2"]) if "buy_t2" in gann else float(
            gann["buy_targets"][1])

        sell_t1 = float(gann["sell_t1"]) if "sell_t1" in gann else float(
            gann["sell_targets"][0])
        sell_t2 = float(gann["sell_t2"]) if "sell_t2" in gann else float(
            gann["sell_targets"][1])

        buy_at = float(gann["buy_at"])
        sell_at = float(gann["sell_at"])

        buy_t05 = round((buy_at + buy_t1) / 2.0, 5)
        sell_t05 = round((sell_at + sell_t1) / 2.0, 5)

        buy_t15 = round((buy_t1 + buy_t2) / 2.0, 5)
        sell_t15 = round((sell_t1 + sell_t2) / 2.0, 5)

        return {
            "buy_at": buy_at,
            "buy_t05": buy_t05,
            "buy_t1": buy_t1,
            "buy_t2": buy_t2,
            "buy_t15": buy_t15,
            "sell_at": sell_at,
            "sell_t05": sell_t05,
            "sell_t1": sell_t1,
            "sell_t2": sell_t2,
            "sell_t15": sell_t15,
        }

    @staticmethod
    def build_custom_setup(
        side: str,
        entry: float,
        sl: float,
        tp: float,
        fund: float,
        risk_percent: float,
        pair: str,
        entry_mode: str,
        target_mode: str,
    ) -> Dict:
        sl_pips = StrategyCalculator._calc_sl_pips(entry, sl, pair)
        lot = StrategyCalculator.calculate_lot_size(
            fund, risk_percent, sl_pips, pair, entry
        )

        return {
            "side": side,
            "entry": round(float(entry), 5),
            "sl": round(float(sl), 5),
            "tp": round(float(tp), 5),
            "sl_pips": round(float(sl_pips), 1),
            "lot_size": lot,
            "entry_mode": entry_mode,
            "target_mode": target_mode,
        }

    @staticmethod
    def build_buy_from_buyat(gann: Dict, fund: float, risk_percent: float, pair: str) -> Dict:
        lv = StrategyCalculator._extract_levels(gann)
        return StrategyCalculator.build_custom_setup(
            side="B",
            entry=lv["buy_at"],
            sl=lv["sell_at"],
            tp=lv["buy_t1"],
            fund=fund,
            risk_percent=risk_percent,
            pair=pair,
            entry_mode="BUY_AT",
            target_mode="T1",
        )

    @staticmethod
    def build_buy_from_buy_t1(gann: Dict, fund: float, risk_percent: float, pair: str) -> Dict:
        lv = StrategyCalculator._extract_levels(gann)
        return StrategyCalculator.build_custom_setup(
            side="B",
            entry=lv["buy_t1"],
            sl=lv["buy_at"],
            tp=lv["buy_t15"],
            fund=fund,
            risk_percent=risk_percent,
            pair=pair,
            entry_mode="BUY_T1",
            target_mode="T15",
        )

    @staticmethod
    def build_sell_from_sellat(gann: Dict, fund: float, risk_percent: float, pair: str) -> Dict:
        lv = StrategyCalculator._extract_levels(gann)
        return StrategyCalculator.build_custom_setup(
            side="S",
            entry=lv["sell_at"],
            sl=lv["buy_at"],
            tp=lv["sell_t1"],
            fund=fund,
            risk_percent=risk_percent,
            pair=pair,
            entry_mode="SELL_AT",
            target_mode="T1",
        )

    @staticmethod
    def build_sell_from_sell_t1(gann: Dict, fund: float, risk_percent: float, pair: str) -> Dict:
        lv = StrategyCalculator._extract_levels(gann)
        return StrategyCalculator.build_custom_setup(
            side="S",
            entry=lv["sell_t1"],
            sl=lv["sell_at"],
            tp=lv["sell_t15"],
            fund=fund,
            risk_percent=risk_percent,
            pair=pair,
            entry_mode="SELL_T1",
            target_mode="T15",
        )
