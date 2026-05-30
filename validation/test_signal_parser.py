"""
validation/test_signal_parser.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Unit tests for the SignalParser and all three filter gates.

Run from the project root:
    python -m pytest validation/test_signal_parser.py -v

Or directly:
    python validation/test_signal_parser.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python-brain'))

import unittest
from signal_parser_standalone import SignalParser, TradingSignal
from market_analyzer import (
    PivotPointFilter, RSIFilter, NewsSentimentFilter,
    determine_lot_size, Gate, Sentiment,
    LOT_NEWS_MODE, LOT_PARTIAL, LOT_STANDARD, LOT_FULL_CONFLUENCE,
    PIVOT_BUFFER_USD,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SIGNAL PARSER TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestSignalParser(unittest.TestCase):

    def _parse(self, msg):
        from telegram_listener import SignalParser as SP
        return SP.parse_signal(msg)

    def test_format_inline_buy(self):
        msg = "BUY XAUUSD @ 2450.50, SL: 2445.00, TP: 2460.00"
        sig = self._parse(msg)
        self.assertIsNotNone(sig, "Should parse inline BUY")
        self.assertEqual(sig.signal_type, "BUY")
        self.assertAlmostEqual(sig.entry_price, 2450.50)
        self.assertAlmostEqual(sig.stop_loss, 2445.00)
        self.assertAlmostEqual(sig.take_profit, 2460.00)

    def test_format_inline_sell(self):
        msg = "SELL XAU/USD @ 2450.50, SL: 2455.00, TP: 2440.00"
        sig = self._parse(msg)
        self.assertIsNotNone(sig, "Should parse inline SELL")
        self.assertEqual(sig.signal_type, "SELL")

    def test_format_multiline(self):
        msg = "BUY XAUUSD\n2450.50\nSL: 2445.00\nTP: 2460.00"
        sig = self._parse(msg)
        self.assertIsNotNone(sig, "Should parse multiline BUY")
        self.assertEqual(sig.signal_type, "BUY")

    def test_format_alt_layout(self):
        msg = "XAUUSD\nBUY @ 2450.50\nS/L 2445.00\nT/P 2460.00"
        sig = self._parse(msg)
        self.assertIsNotNone(sig, "Should parse alt layout")

    def test_format_concise(self):
        msg = "#BUY 2450.50 SL2445 TP2460"
        sig = self._parse(msg)
        self.assertIsNotNone(sig, "Should parse concise format")

    def test_invalid_buy_sl_above_entry(self):
        msg = "BUY XAUUSD @ 2450, SL: 2460, TP: 2470"
        sig = self._parse(msg)
        self.assertIsNone(sig, "BUY with SL > Entry should be rejected")

    def test_invalid_sell_sl_below_entry(self):
        msg = "SELL XAUUSD @ 2450, SL: 2440, TP: 2430"
        sig = self._parse(msg)
        self.assertIsNone(sig, "SELL with SL < Entry should be rejected")

    def test_non_xauusd_rejected(self):
        msg = "BUY EURUSD @ 1.0850, SL: 1.0800, TP: 1.0900"
        sig = self._parse(msg)
        self.assertIsNone(sig, "Non-XAUUSD should be rejected")

    def test_price_out_of_range_rejected(self):
        msg = "BUY XAUUSD @ 500.00, SL: 490.00, TP: 520.00"
        sig = self._parse(msg)
        self.assertIsNone(sig, "Price below XAUUSD_PRICE_MIN should be rejected")

    def test_missing_sl_tp_rejected(self):
        msg = "BUY XAUUSD @ 2450"
        sig = self._parse(msg)
        self.assertIsNone(sig, "Signal without SL/TP should be rejected")

    def test_confidence_score_rr_based(self):
        msg = "BUY XAUUSD @ 2450, SL: 2445, TP: 2465"
        sig = self._parse(msg)
        self.assertIsNotNone(sig)
        self.assertGreater(sig.confidence_score, 0.5, "Higher RR should yield higher confidence")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PIVOT POINT FILTER TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestPivotPointFilter(unittest.TestCase):

    def setUp(self):
        # Previous day: High=2460, Low=2440, Close=2450
        # Pivot = (2460+2440+2450)/3 = 2450
        # R1    = (2*2450) - 2440   = 2460
        # S1    = (2*2450) - 2460   = 2440
        self.pivots = PivotPointFilter.calculate(2460.0, 2440.0, 2450.0)
        self.r1 = self.pivots['r1']  # 2460
        self.s1 = self.pivots['s1']  # 2440

    def test_pivot_values_correct(self):
        self.assertAlmostEqual(self.pivots['pivot'], 2450.0)
        self.assertAlmostEqual(self.r1, 2460.0)
        self.assertAlmostEqual(self.s1, 2440.0)

    def test_buy_near_r1_is_blocked(self):
        """BUY within PIVOT_BUFFER_USD of R1 must be BLOCKED."""
        entry = self.r1 - (PIVOT_BUFFER_USD * 0.5)  # Just inside buffer
        gate, reason, score = PivotPointFilter.validate('BUY', entry, self.pivots)
        self.assertEqual(gate, Gate.BLOCK, f"BUY near R1 should be BLOCKED. Reason: {reason}")
        self.assertEqual(score, 0.0)

    def test_buy_near_s1_passes(self):
        """BUY within PIVOT_BUFFER_USD of S1 must PASS (S1 bounce)."""
        entry = self.s1 + (PIVOT_BUFFER_USD * 0.5)
        gate, reason, score = PivotPointFilter.validate('BUY', entry, self.pivots)
        self.assertEqual(gate, Gate.PASS, f"BUY near S1 should PASS. Reason: {reason}")
        self.assertGreater(score, 0.8)

    def test_sell_near_s1_is_blocked(self):
        """SELL within PIVOT_BUFFER_USD of S1 must be BLOCKED."""
        entry = self.s1 + (PIVOT_BUFFER_USD * 0.5)
        gate, reason, score = PivotPointFilter.validate('SELL', entry, self.pivots)
        self.assertEqual(gate, Gate.BLOCK, f"SELL near S1 should be BLOCKED. Reason: {reason}")

    def test_sell_near_r1_passes(self):
        """SELL within PIVOT_BUFFER_USD of R1 must PASS (R1 rejection)."""
        entry = self.r1 - (PIVOT_BUFFER_USD * 0.5)
        gate, reason, score = PivotPointFilter.validate('SELL', entry, self.pivots)
        self.assertEqual(gate, Gate.PASS, f"SELL near R1 should PASS. Reason: {reason}")

    def test_buy_neutral_zone_warns(self):
        """BUY far from both R1 and S1 should WARN."""
        entry = (self.s1 + self.r1) / 2  # Middle of range, 2450
        gate, reason, score = PivotPointFilter.validate('BUY', entry, self.pivots)
        self.assertEqual(gate, Gate.WARN)

    def test_sell_neutral_zone_warns(self):
        """SELL in neutral zone should WARN."""
        entry = (self.s1 + self.r1) / 2
        gate, reason, score = PivotPointFilter.validate('SELL', entry, self.pivots)
        self.assertEqual(gate, Gate.WARN)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RSI FILTER TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestRSIFilter(unittest.TestCase):

    def test_buy_blocked_when_overbought(self):
        gate, reason, score = RSIFilter.validate_buy(76.0)
        self.assertEqual(gate, Gate.BLOCK)
        self.assertEqual(score, 0.0)

    def test_buy_passes_normal(self):
        gate, reason, score = RSIFilter.validate_buy(50.0)
        self.assertEqual(gate, Gate.PASS)

    def test_buy_passes_oversold(self):
        gate, reason, score = RSIFilter.validate_buy(22.0)
        self.assertEqual(gate, Gate.PASS)
        self.assertGreater(score, 0.9)

    def test_sell_blocked_when_oversold(self):
        gate, reason, score = RSIFilter.validate_sell(24.0)
        self.assertEqual(gate, Gate.BLOCK)
        self.assertEqual(score, 0.0)

    def test_sell_passes_normal(self):
        gate, reason, score = RSIFilter.validate_sell(55.0)
        self.assertEqual(gate, Gate.PASS)

    def test_sell_passes_overbought(self):
        gate, reason, score = RSIFilter.validate_sell(78.0)
        self.assertEqual(gate, Gate.PASS)
        self.assertGreater(score, 0.9)

    def test_rsi_computation_wilder(self):
        """RSI of a pure uptrend should be near 100."""
        closes = [100.0 + i for i in range(20)]
        rsi = RSIFilter.compute_rsi(closes)
        self.assertGreater(rsi, 90.0, "Pure uptrend should give RSI near 100")

    def test_rsi_computation_neutral(self):
        """Alternating up/down should give RSI near 50."""
        closes = []
        p = 2450.0
        for i in range(20):
            p = p + 1.0 if i % 2 == 0 else p - 1.0
            closes.append(p)
        rsi = RSIFilter.compute_rsi(closes)
        self.assertGreater(rsi, 40.0)
        self.assertLess(rsi, 60.0)

    def test_rsi_insufficient_data_returns_neutral(self):
        rsi = RSIFilter.compute_rsi([2450.0] * 5)
        self.assertEqual(rsi, 50.0)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOT SIZE ENGINE TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestLotSizeEngine(unittest.TestCase):

    def test_news_mode_always_returns_minimum(self):
        for confluence in range(4):
            lot = determine_lot_size(confluence, news_mode_active=True)
            self.assertEqual(lot, LOT_NEWS_MODE,
                f"News_Mode must always return 0.01 regardless of confluence={confluence}")

    def test_zero_confluence_returns_minimum(self):
        lot = determine_lot_size(0, news_mode_active=False)
        self.assertEqual(lot, LOT_NEWS_MODE)

    def test_partial_confluence_returns_002(self):
        lot = determine_lot_size(1, news_mode_active=False)
        self.assertEqual(lot, LOT_PARTIAL)

    def test_standard_confluence_returns_003(self):
        lot = determine_lot_size(2, news_mode_active=False)
        self.assertEqual(lot, LOT_STANDARD)

    def test_full_confluence_returns_005(self):
        lot = determine_lot_size(3, news_mode_active=False)
        self.assertEqual(lot, LOT_FULL_CONFLUENCE)

    def test_all_lots_within_envelope(self):
        for c in range(4):
            for nm in [True, False]:
                lot = determine_lot_size(c, nm)
                self.assertGreaterEqual(lot, 0.01)
                self.assertLessEqual(lot, 0.05)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NEWS SENTIMENT FILTER TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestNewsSentimentFilter(unittest.TestCase):

    def test_high_impact_activates_news_mode(self):
        f = NewsSentimentFilter()
        _, triggered, _ = f.analyze("FOMC meeting today — rate decision imminent.")
        self.assertTrue(triggered)
        self.assertTrue(f.is_news_mode_active())

    def test_bullish_sentiment_detected(self):
        f = NewsSentimentFilter()
        sentiment, _, _ = f.analyze("Fed rate cut expected. Dollar weakness persists amid geopolitical tensions.")
        self.assertEqual(sentiment, Sentiment.BULLISH)

    def test_bearish_sentiment_detected(self):
        f = NewsSentimentFilter()
        sentiment, _, _ = f.analyze("Strong GDP growth. Dollar strong. Rate hike expected by hawkish Fed.")
        self.assertEqual(sentiment, Sentiment.BEARISH)

    def test_neutral_on_irrelevant_news(self):
        f = NewsSentimentFilter()
        sentiment, triggered, _ = f.analyze("Local sports team wins championship match today.")
        self.assertEqual(sentiment, Sentiment.NEUTRAL)
        self.assertFalse(triggered)

    def test_news_mode_inactive_by_default(self):
        f = NewsSentimentFilter()
        self.assertFalse(f.is_news_mode_active())


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RUN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestSignalParser))
    suite.addTests(loader.loadTestsFromTestCase(TestPivotPointFilter))
    suite.addTests(loader.loadTestsFromTestCase(TestRSIFilter))
    suite.addTests(loader.loadTestsFromTestCase(TestLotSizeEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestNewsSentimentFilter))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
