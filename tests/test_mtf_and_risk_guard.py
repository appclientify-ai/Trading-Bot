import unittest
import time
from datetime import datetime, timezone, timedelta
from database import init_db, get_connection
from smc_engine import analyze_4angle_mtf, analyze_smc_setup, COMMODITY_SPECS
from telegram_bot import generate_eod_report

class TestMTFAndRiskGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def test_01_mtf_4angle_radar_structure(self):
        """Test that 4-Angle MTF radar returns valid structure across 1H, 30m, 15m, 5m."""
        res = analyze_4angle_mtf('crude', 9500.0, 'CL=F')
        self.assertIn('trend_1h', res)
        self.assertIn('equilibrium_30m', res)
        self.assertIn('atr14', res)
        self.assertIn('vol_ratio', res)
        self.assertIn('radar_table', res)
        self.assertIn('1-Hour (1H)', res['radar_table'])
        self.assertIn('30-Min (30m)', res['radar_table'])
        self.assertIn('15-Min (15m)', res['radar_table'])
        self.assertIn('5-Min (5m)', res['radar_table'])
        self.assertIsInstance(res['buy_traps'], list)
        self.assertIsInstance(res['sell_traps'], list)

    def test_02_smc_setup_gating_and_trap_warning(self):
        """Test that SMC setup enforces 4-Angle MTF and includes radar in card text."""
        # On-demand force_tip returns setup with 4-angle radar
        setup = analyze_smc_setup('crude', custom_cmp=9500.0, force_tip=True)
        self.assertTrue(setup.get('has_setup'))
        card = setup.get('card_text', '')
        self.assertIn("4-ANGLE MULTI-TIMEFRAME RADAR", card)
        self.assertIn("1-Hour (1H)", card)
        self.assertIn("5-Min (5m)", card)

    def test_03_clean_eod_report_format(self):
        """Test that EOD report outputs executive scorecard, 2-line trade log, and commodity emojis."""
        ist = timezone(timedelta(hours=5, minutes=30))
        today_str = datetime.now(ist).strftime("%Y-%m-%d")

        # Insert test trade
        tid = f"TEST_AUDIT_{int(time.time())}"
        with get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO trades (trade_id, name, direction, entry, t1, sl, t1_profit, sl_risk,
                                               prob_pct, reasons_str, status, t1_hit, sl_hit, user_taken, date, timestamp)
                VALUES (?, 'CRUDE OIL', 'BUY', 9500, 9550, 9460, 5000, 4000, 88, 'SMC Liquidity Sweep', 'T1_HIT', 1, 0, 1, ?, ?)
            """, (tid, today_str, int(time.time())))
            conn.commit()

        report = generate_eod_report(today_str=today_str)
        self.assertIn("MCX POST-MARKET EXECUTIVE AUDIT", report)
        self.assertIn("PERFORMANCE SCORECARD", report)
        self.assertIn("CRUDE OIL", report)
        self.assertIn("🛢️", report)
        self.assertIn("WIN +₹5,000", report)
        self.assertNotIn("ACTIVE / IN PROGRESS", report)
        self.assertNotIn("IN PROGRESS", report)

if __name__ == '__main__':
    unittest.main()
