import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import telegram_bot
from smc_engine import analyze_smc_setup, check_active_trade_reversal
from mcx_feed import get_live_mcx_price

class TestTradingBotWorkflow(unittest.TestCase):
    def test_mcx_feed_natgas(self):
        price = get_live_mcx_price("natgas")
        self.assertIsInstance(price, float)
        self.assertGreater(price, 0.0)

    def test_smc_analysis(self):
        res = analyze_smc_setup("crude")
        self.assertIn("card_text", res)
        self.assertIn("cmp", res)

    def test_trade_reversal_check(self):
        chk = check_active_trade_reversal("natgas", "BUY", 288.0, 285.0, 294.0, custom_cmp=289.0)
        self.assertIn("cmp", chk)
        self.assertIn("pnl_inr", chk)
        self.assertEqual(chk["cmp"], 289.0)

if __name__ == "__main__":
    unittest.main()
