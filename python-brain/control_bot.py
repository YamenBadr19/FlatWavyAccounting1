"""
ControlBot — Telegram Control Bot  (python-telegram-bot ≥20)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Separate bot (BotFather token) from the userbot.
Requires: CONTROL_BOT_TOKEN, optionally CONTROL_BOT_CHAT_ID.

Commands:
  /start     — Resume signal processing
  /stop      — Pause signal processing (MCP stays connected)
  /status    — Live system health: MCP connection, price, balance, mode
  /balance   — Current equity, free margin, risk settings, data source
  /positions — All open positions with live P&L via cTrader MCP
"""

import asyncio
import logging
import os

logger = logging.getLogger('ctrl_bot')

CONTROL_BOT_TOKEN   = os.environ.get("CONTROL_BOT_TOKEN",   "").strip()
CONTROL_BOT_CHAT_ID = os.environ.get("CONTROL_BOT_CHAT_ID", "").strip()


class ControlBot:
    """
    Manages a python-telegram-bot Application alongside the asyncio brain.
    Exposes is_paused() which ExecutionBridge checks before every execution.
    """

    def __init__(
        self,
        balance_manager = None,
        fix_executor    = None,
        news_feed       = None,
        market_feed     = None,
    ):
        self._balance_mgr  = balance_manager
        self._executor     = fix_executor   # MCPExecutor (kept as fix_executor for compat)
        self._news_feed    = news_feed
        self._market_feed  = market_feed
        self._paused       = False
        self._app          = None

    def is_paused(self) -> bool:
        """Returns True when the operator has issued /stop."""
        return self._paused

    async def run_forever(self):
        if not CONTROL_BOT_TOKEN:
            logger.warning(
                "CONTROL_BOT_TOKEN not set — control bot disabled. "
                "Create a bot via @BotFather and add the token to Replit Secrets."
            )
            return

        try:
            from telegram import Update
            from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
        except ImportError:
            logger.warning(
                "python-telegram-bot not installed — control bot disabled. "
                "Run: pip install python-telegram-bot"
            )
            return

        me = self

        def _authorized(update: Update) -> bool:
            if not CONTROL_BOT_CHAT_ID:
                return True
            return str(update.effective_chat.id) == CONTROL_BOT_CHAT_ID

        async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if not _authorized(update):
                await update.message.reply_text("⛔ Unauthorized")
                return
            me._paused = False
            await update.message.reply_text(
                "✅ *Gold Blueprint* — Signal processing **RESUMED**",
                parse_mode="Markdown"
            )
            logger.info(f"[CTRL] /start by {update.effective_user.username}")

        async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if not _authorized(update):
                await update.message.reply_text("⛔ Unauthorized")
                return
            me._paused = True
            await update.message.reply_text(
                "⏸ *Gold Blueprint* — Signal processing **PAUSED**\n"
                "_MCP connection remains active. Send /start to resume._",
                parse_mode="Markdown"
            )
            logger.info(f"[CTRL] /stop by {update.effective_user.username}")

        async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if not _authorized(update):
                return
            exec_stats = me._executor.stats() if me._executor else {}
            bal        = me._balance_mgr.status()  if me._balance_mgr  else {}
            snap       = me._market_feed.snapshot  if me._market_feed   else None
            price_line = f"`{snap.current_price:.2f}`" if (snap and snap.current_price > 0) else "N/A"
            mcp_ok     = "✅" if exec_stats.get("mcp_connected") else "❌"
            mode       = "⏸ PAUSED" if me._paused else "✅ ACTIVE"
            text = (
                f"*Gold Blueprint — Live Status*\n"
                f"{'━'*30}\n"
                f"🔁 Processing: {mode}\n"
                f"🔌 MCP (cTrader): {mcp_ok}\n"
                f"💛 XAUUSD: {price_line}\n"
                f"💰 Equity: `${bal.get('equity', 0):,.2f}`  ({bal.get('source','?')})\n"
                f"📊 Executed: {exec_stats.get('executed', 0)}  "
                f"Rejected: {exec_stats.get('rejected', 0)}\n"
                f"📂 Open tracked: {exec_stats.get('open_positions', 0)}\n"
                f"{'━'*30}"
            )
            await update.message.reply_text(text, parse_mode="Markdown")

        async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if not _authorized(update):
                return
            bs = me._balance_mgr.status() if me._balance_mgr else {}
            text = (
                f"*Balance & Risk Settings*\n"
                f"{'━'*30}\n"
                f"💰 Equity:       `${bs.get('equity', 0):,.2f}`\n"
                f"📐 Free Margin:  `${bs.get('free_margin', 0):,.2f}`\n"
                f"📊 Risk/trade:   `{bs.get('risk_pct', 1.0)}%`\n"
                f"📦 Lot range:    `{bs.get('min_lot',0.01)} – {bs.get('max_lot',0.05)}`\n"
                f"🔗 Data source:  `{bs.get('source', 'DEFAULT')}`\n"
                f"🌐 MCP:          {'✅ Live' if bs.get('mcp_connected') else '⚠️ Offline (using default equity)'}"
            )
            await update.message.reply_text(text, parse_mode="Markdown")

        async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            """
            Query get_positions from the cTrader MCP server and render
            each open position with direction, volume, entry, current P&L,
            and current SL/TP.
            """
            if not _authorized(update):
                return

            if not me._executor:
                await update.message.reply_text("⚠️ Executor not available")
                return

            await update.message.reply_text("🔄 Fetching open positions from cTrader…")

            try:
                raw = await me._executor._client.call("get_positions")
            except Exception as e:
                await update.message.reply_text(
                    f"❌ *MCP Error*: `{e}`\n\n"
                    "_Is cTrader open with Local MCP enabled on port 9876?_",
                    parse_mode="Markdown",
                )
                return

            # Normalise: MCP may return a list directly or wrap it
            if isinstance(raw, dict):
                positions = raw.get("positions", raw.get("data", [raw]))
            elif isinstance(raw, list):
                positions = raw
            else:
                positions = []

            if not positions:
                snap  = me._market_feed.snapshot if me._market_feed else None
                price = f"${snap.current_price:,.2f}" if (snap and snap.current_price > 0) else "N/A"
                await update.message.reply_text(
                    f"📭 *No open positions*\n"
                    f"XAUUSD spot: {price}",
                    parse_mode="Markdown",
                )
                return

            # Get current price for P&L calculation
            snap  = me._market_feed.snapshot if me._market_feed else None
            spot  = snap.current_price if (snap and snap.current_price > 0) else 0.0

            lines = [f"*Open Positions ({len(positions)})*", f"{'━'*30}"]

            for pos in positions:
                # Field names vary between cTrader MCP versions — try all variants
                pos_id    = pos.get("positionId",  pos.get("id",          "?"))
                symbol    = pos.get("symbolName",  pos.get("symbol",      "XAUUSD"))
                direction = pos.get("tradeType",   pos.get("direction",   pos.get("side", "?")))
                volume    = float(pos.get("volume", pos.get("lots",       pos.get("size", 0))))
                entry     = float(pos.get("entryPrice", pos.get("openPrice", pos.get("price", 0))))
                sl        = float(pos.get("stopLoss",   pos.get("sl",  0)))
                tp        = float(pos.get("takeProfit", pos.get("tp",  0)))
                pnl_raw   = pos.get("unrealizedGrossProfit",
                            pos.get("unrealizedPnl",
                            pos.get("pnl", None)))

                # Calculate P&L if not provided by MCP
                if pnl_raw is not None:
                    pnl = float(pnl_raw)
                elif spot > 0 and entry > 0 and volume > 0:
                    if str(direction).upper() in ("BUY", "LONG", "0"):
                        pnl = (spot - entry) * volume * 100.0
                    else:
                        pnl = (entry - spot) * volume * 100.0
                else:
                    pnl = 0.0

                dir_icon  = "🟢" if str(direction).upper() in ("BUY", "LONG", "0") else "🔴"
                pnl_icon  = "📈" if pnl >= 0 else "📉"
                pnl_str   = f"`${pnl:+,.2f}`"
                sl_str    = f"`{sl:,.2f}`" if sl else "_none_"
                tp_str    = f"`{tp:,.2f}`" if tp else "_none_"

                lines.append(
                    f"{dir_icon} *{str(direction).upper()}* {volume}L {symbol}\n"
                    f"  Entry: `{entry:,.2f}`  |  Spot: `{spot:,.2f}`\n"
                    f"  {pnl_icon} P&L: {pnl_str}\n"
                    f"  SL: {sl_str}  TP: {tp_str}\n"
                    f"  ID: `{pos_id}`"
                )

            lines.append(f"{'━'*30}")
            lines.append(f"_Spot price: ${spot:,.2f}_")

            await update.message.reply_text(
                "\n".join(lines),
                parse_mode="Markdown",
            )
            logger.info(f"[CTRL] /positions returned {len(positions)} positions")

        self._app = ApplicationBuilder().token(CONTROL_BOT_TOKEN).build()
        self._app.add_handler(CommandHandler("start",     cmd_start))
        self._app.add_handler(CommandHandler("stop",      cmd_stop))
        self._app.add_handler(CommandHandler("status",    cmd_status))
        self._app.add_handler(CommandHandler("balance",   cmd_balance))
        self._app.add_handler(CommandHandler("positions", cmd_positions))

        logger.info("Control bot polling for commands (LIVE)")
        try:
            async with self._app:
                await self._app.start()
                await self._app.updater.start_polling(drop_pending_updates=True)
                await asyncio.Event().wait()
                await self._app.updater.stop()
                await self._app.stop()
        except Exception as e:
            logger.error(f"Control bot error: {e}", exc_info=True)
