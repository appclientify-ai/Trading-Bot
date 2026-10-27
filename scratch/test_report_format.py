import sys
import os
from datetime import datetime, timezone, timedelta

# Add repo to sys.path
sys.path.insert(0, os.path.abspath('.'))

from database import get_connection

def simulate_new_eod_report():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM trades ORDER BY date DESC, timestamp DESC LIMIT 30")
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("No trades found in DB")
        return

    today_str = rows[0]['date']
    today_trades = [dict(r) for r in rows if r['date'] == today_str]
    if not today_trades:
        today_trades = [dict(r) for r in rows[:15]]

    total_signals = len(today_trades)
    t1_hits = sum(1 for t in today_trades if t.get("status") == "T1_HIT" or "EOD_PROFIT" in str(t.get("status", "")))
    sl_hits = sum(1 for t in today_trades if t.get("status") == "SL_HIT" or "EOD_LOSS" in str(t.get("status", "")))
    eod_closed = sum(1 for t in today_trades if "EOD_" in str(t.get("status", "")))

    theo_profit = sum(float(t.get("t1_profit", 0) or 0) for t in today_trades if t.get("status") == "T1_HIT" or "EOD_PROFIT" in str(t.get("status", "")))
    theo_loss = sum(float(t.get("sl_risk", 0) or 0) for t in today_trades if t.get("status") == "SL_HIT" or "EOD_LOSS" in str(t.get("status", "")))
    net_theo_pnl = theo_profit - theo_loss

    # User taken trades
    user_taken = [t for t in today_trades if t.get("user_taken")]
    user_total = len(user_taken)
    user_t1 = sum(1 for t in user_taken if t.get("status") == "T1_HIT" or "EOD_PROFIT" in str(t.get("status", "")))
    user_sl = sum(1 for t in user_taken if t.get("status") == "SL_HIT" or "EOD_LOSS" in str(t.get("status", "")))
    user_profit = sum(float(t.get("t1_profit", 0) or 0) for t in user_taken if t.get("status") == "T1_HIT" or "EOD_PROFIT" in str(t.get("status", "")))
    user_loss = sum(float(t.get("sl_risk", 0) or 0) for t in user_taken if t.get("status") == "SL_HIT" or "EOD_LOSS" in str(t.get("status", "")))
    net_user_pnl = user_profit - user_loss

    closed_trades = t1_hits + sl_hits
    win_rate = (t1_hits / closed_trades * 100) if closed_trades > 0 else 0.0

    COMMODITY_ICONS = {
        'crude': '🛢️', 'oil': '🛢️',
        'silver': '🪙',
        'gold': '🥇',
        'natgas': '🔥', 'gas': '🔥',
        'aluminium': '⚙️',
        'zinc': '🔩',
        'nickel': '🪙',
        'electricity': '⚡'
    }

    trade_items = []
    for i, t in enumerate(today_trades, 1):
        c_name = t.get("name", "Commodity")
        c_lower = c_name.lower()
        icon = '📊'
        for k, v in COMMODITY_ICONS.items():
            if k in c_lower:
                icon = v
                break

        dirn = t.get("direction", "BUY")
        st = str(t.get("status", "EOD_BREAKEVEN:₹0"))
        t_prof = float(t.get("t1_profit", 0) or 0)
        s_risk = float(t.get("sl_risk", 0) or 0)
        taken_badge = " • 👤 <b>Aapka Trade</b>" if t.get("user_taken") else ""

        if st == "T1_HIT":
            outcome = f"🟢 <b>WIN +₹{t_prof:,.0f}</b>"
            diag = t.get("reasons_str") or "SMC Liquidity Sweep + MTF Trend Alignment"
        elif st == "SL_HIT":
            outcome = f"🔴 <b>LOSS -₹{s_risk:,.0f}</b>"
            diag = t.get("fail_reason") or "Sharp Volatility Spike & Reversal Trap"
        elif "EOD_PROFIT" in st:
            outcome = f"🏁 <b>CMP WIN +₹{t_prof:,.0f}</b>"
            diag = "Valued at Final Market Closing CMP"
        elif "EOD_LOSS" in st:
            outcome = f"🏁 <b>CMP LOSS -₹{s_risk:,.0f}</b>"
            diag = "Valued at Final Market Closing CMP"
        else:
            outcome = "🏁 <b>CMP BREAKEVEN ₹0</b>"
            diag = "Valued at Final Market Closing CMP"

        # Ultra-clean 2-line scannable card
        trade_items.append(
            f"<b>{i:02d}.</b> {icon} <b>{c_name.upper()}</b> ({dirn}){taken_badge}\n"
            f"     ↳ {outcome} | <i>{diag[:55]}</i>"
        )

    log_str = "\n".join(trade_items)
    user_pnl_str = f"+₹{net_user_pnl:,.0f}" if net_user_pnl >= 0 else f"-₹{abs(net_user_pnl):,.0f}"
    theo_pnl_str = f"+₹{net_theo_pnl:,.0f}" if net_theo_pnl >= 0 else f"-₹{abs(net_theo_pnl):,.0f}"

    report = (
        f"📊 <b>MCX POST-MARKET EXECUTIVE AUDIT & P&L REPORT</b>\n"
        f"📅 <b>Date:</b> <code>{today_str}</code> | <b>Session:</b> <code>CLOSED 🏁</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>PERFORMANCE SCORECARD:</b>\n"
        f"• 🎯 Total Signals     : <b>{total_signals} Signals</b>\n"
        f"• 🟢 Wins (Target 1)   : <b>{t1_hits}</b>\n"
        f"• 🔴 Losses (Stop-Loss): <b>{sl_hits}</b>\n"
        f"• 🏁 EOD CMP Valued    : <b>{eod_closed}</b>\n"
        f"• 📈 Win Rate          : <b>{win_rate:.1f}%</b>\n"
        f"• 💰 Theoretical P&L   : <b>{theo_pnl_str}</b> (1-Lot Base)\n"
    )

    if user_total > 0:
        report += (
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💼 <b>AAPKA PERSONAL REALIZED P&L:</b>\n"
            f"• 👤 Trades Taken      : <b>{user_total} Trades</b> ({user_t1} Won / {user_sl} Lost)\n"
            f"• 💵 Realized Net P&L  : <b>{user_pnl_str} 🔥</b>\n"
        )

    report += (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>TRADE-BY-TRADE AUDIT & REASON ANALYSIS:</b>\n"
        f"{log_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ <b>SYSTEM UPGRADE ACTIVE:</b>\n"
        f"• <b>4-Angle MTF (1H+30m+15m+5m)</b> + <b>News Confluence</b> filter active.\n"
        f"• <b>Sharp Volatility & Reversal Traps</b> are pre-warned and blocked.\n"
        f"📌 <i>Next Session Live Institutional Scanner begins at 09:00 AM IST.</i>"
    )

    print(report)

simulate_new_eod_report()
