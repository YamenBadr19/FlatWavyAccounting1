"""
ControlBot — Telegram Control Bot  (python-telegram-bot ≥20)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Separate bot (BotFather token) from the userbot.
Requires: CONTROL_BOT_TOKEN, optionally CONTROL_BOT_CHAT_ID.

Commands:
  /start    — Resume signal processing
  /stop     — Pause signal processing (MCP stays connected)
  /status   — Live system health: MCP connection, price, balance, mode
  /balance  — Current equity, free margin, risk settings, data source
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

        self._app = ApplicationBuilder().token(CONTROL_BOT_TOKEN).build()
        self._app.add_handler(CommandHandler("start",   cmd_start))
        self._app.add_handler(CommandHandler("stop",    cmd_stop))
        self._app.add_handler(CommandHandler("status",  cmd_status))
        self._app.add_handler(CommandHandler("balance", cmd_balance))

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
