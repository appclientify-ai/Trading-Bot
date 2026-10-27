import unittest
import os
import sys
import time
from datetime import datetime, timezone, timedelta

# Ensure parent directory is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from smc_engine import check_active_trade_reversal
from database import (
    evaluate_and_sync_active_trades,
    save_trade_db,
    get_db_config,
    set_db_config,
    get_connection
)
from telegram_bot import (
    generate_eod_report,
    check_daily_kill_switch,
    broadcast_eod_hold_advisory,
    check_economic_calendar_events
)


class TestNewTradingFeatures(unittest.TestCase):

    def test_01_trailing_sl_at_50_pct(self):
        """When price reaches 50% progress towards T1, trailing_cost_to_cost should be True."""
        # BUY trade: Entry 6000, SL 5900, T1 6200 (Total target distance: 200 pts)
        # 50% move is 6100. Let's test at 6110 (55% progress)
        chk = check_active_trade_reversal("crude", "BUY", 6000.0, 5900.0, 6200.0, custom_cmp=6110.0)
        self.assertTrue(chk.get("trailing_cost_to_cost"))
        self.assertEqual(chk.get("trailing_sl_price"), 6000.0) # SL moved to Entry
        self.assertGreaterEqual(chk.get("progress_pct"), 50.0)

        # Before 50%: e.g. at 6040 (20% progress)
        chk_early = check_active_trade_reversal("crude", "BUY", 6000.0, 5900.0, 6200.0, custom_cmp=6040.0)
        self.assertFalse(chk_early.get("trailing_cost_to_cost"))
        self.assertEqual(chk_early.get("trailing_sl_price"), 5900.0)

    def test_02_multi_lot_scaling_at_target_1(self):
        """When T1 is reached, multi-lot advice should be returned."""
        chk = check_active_trade_reversal("crude", "BUY", 6000.0, 5900.0, 6200.0, custom_cmp=6205.0)
        self.assertTrue(chk.get("is_t1"))
        self.assertIn("BOOK 50% LOTS", chk.get("multi_lot_advice", ""))

    def test_03_daily_kill_switch(self):
        """Kill switch should activate when loss reaches or exceeds max daily loss."""
        ist = timezone(timedelta(hours=5, minutes=30))
        today_str = datetime.now(ist).strftime("%Y-%m-%d")

        # Set limit to 2000
        set_db_config("max_daily_loss", "2000.0")
        set_db_config("kill_switch_active_date", "")

        # Insert a simulated loss trade of 2500 for today
        tid = f"TEST_LOSS_{int(time.time())}"
        with get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO trades (trade_id, name, direction, entry, t1, sl, t1_profit, sl_risk,
                                               prob_pct, reasons_str, status, t1_hit, sl_hit, user_taken, date, timestamp)
                VALUES (?, 'CRUDE OIL', 'BUY', 6000, 6200, 5900, 2000, 2500, 85, 'Test Loss', 'SL_HIT', 0, 1, 1, ?, ?)
            """, (tid, today_str, int(time.time())))
            conn.commit()

        # Check kill switch
        is_killed = check_daily_kill_switch(bot_token=None)
        self.assertTrue(is_killed)
        self.assertEqual(get_db_config("kill_switch_active_date", ""), today_str)

        # Reset kill switch
        set_db_config("kill_switch_active_date", "")
        self.assertEqual(get_db_config("kill_switch_active_date", ""), "")

    def test_04_eod_valuation_no_active_trades(self):
        """EOD report must value unfinished trades by CMP and NEVER output 'ACTIVE' or 'IN PROGRESS'."""
        ist = timezone(timedelta(hours=5, minutes=30))
        today_str = datetime.now(ist).strftime("%Y-%m-%d")

        from mcx_feed import get_live_mcx_price
        cmp_live = get_live_mcx_price("natgas") or 260.0

        # Position is BUY: Entry at CMP - 2, SL far below (CMP - 30), T1 far above (CMP + 30)
        # So price is currently in profit (+2 pts) and neither SL nor T1 is hit!
        entry_test = round(cmp_live - 2.0, 1)
        sl_test = round(cmp_live - 30.0, 1)
        t1_test = round(cmp_live + 30.0, 1)

        # Insert an active unfinished trade in DB
        tid = f"TEST_UNFINISHED_{int(time.time())}"
        with get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO trades (trade_id, name, direction, entry, t1, sl, t1_profit, sl_risk,
                                               prob_pct, reasons_str, status, t1_hit, sl_hit, user_taken, date, timestamp)
                VALUES (?, 'NATURAL GAS', 'BUY', ?, ?, ?, 7500, 7500, 85, 'Test Unfinished', 'ACTIVE', 0, 0, 1, ?, ?)
            """, (tid, entry_test, t1_test, sl_test, today_str, int(time.time())))
            conn.commit()

        # Generate EOD report
        report = generate_eod_report(today_str=today_str)

        # STRICT ASSERTIONS:
        self.assertNotIn("ACTIVE / IN PROGRESS", report)
        self.assertNotIn("IN PROGRESS", report)
        self.assertNotIn("• Active / In Progress", report)
        self.assertIn("CLOSED AT EOD", report)
        self.assertIn("Closed at EOD Valuation", report)


if __name__ == '__main__':
    unittest.main()
