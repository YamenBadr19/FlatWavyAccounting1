"""
ctrader_api.py — cTrader Open API Direct Connection
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
اتصال مباشر بـ cTrader Open API من Replit بدون ngrok.
يستخدم OAuth2 + Protobuf TCP.

المتطلبات في Replit Secrets:
  CTRADER_CLIENT_ID      — Client ID من Open API
  CTRADER_CLIENT_SECRET  — Client Secret
  CTRADER_ACCOUNT_ID     — Account ID (47192877 لـ Deriv Demo)
  CTRADER_ACCESS_TOKEN   — Access Token
"""

import asyncio
import logging
import os
import time
import threading
from typing import Optional, Dict

logger = logging.getLogger('ctrader_api')

CLIENT_ID     = os.environ.get("CTRADER_CLIENT_ID",     "").strip()
CLIENT_SECRET = os.environ.get("CTRADER_CLIENT_SECRET", "").strip()
ACCOUNT_ID    = int(os.environ.get("CTRADER_ACCOUNT_ID", "47192877"))
ACCESS_TOKEN  = os.environ.get("CTRADER_ACCESS_TOKEN",  "").strip()
IS_LIVE       = os.environ.get("CTRADER_LIVE", "false").lower() == "true"


class CTraderOpenAPI:
    """
    عميل مباشر لـ cTrader Open API عبر Protobuf TCP + Twisted.
    يعمل في thread منفصل حتى لا يعارض asyncio الخاص بـ main.py.

    واجهة متوافقة مع MCPExecutor:
      execute_signal(signal)  → dict
      get_account_info()      → dict
      get_positions()         → list
      stats()                 → dict
      run_forever()           → coroutine
    """

    def __init__(self, balance_manager=None):
        self._connected    = False
        self._authorized   = False
        self._balance      = 10000.0
        self._free_margin  = 10000.0
        self._positions:   Dict[int, dict] = {}
        self._executed     = 0
        self._rejected     = 0
        self._last_ok      = 0.0
        self._client       = None
        self._reactor      = None
        self._loop         = None
        self._thread       = None
        self._symbol_map:  Dict[str, int] = {}
        self._balance_mgr  = balance_manager  # مربوط بـ BalanceManager

    @property
    def connected(self) -> bool:
        return self._connected and self._authorized

    # ──────────────────────────────────────────────────────
    # TWISTED THREAD
    # ──────────────────────────────────────────────────────

    def _start_twisted(self):
        """يشغّل Twisted reactor في thread منفصل."""
        from ctrader_open_api import Client, TcpProtocol, EndPoints
        from twisted.internet import reactor

        self._reactor = reactor
        # أنشئ event loop جديد لهذا الـ thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        from ctrader_open_api import EndPoints
        host = EndPoints.PROTOBUF_LIVE_HOST if IS_LIVE else EndPoints.PROTOBUF_DEMO_HOST
        port = EndPoints.PROTOBUF_PORT

        logger.info(f"[cTrader] الاتصال بـ {host}:{port} ({'Live' if IS_LIVE else 'Demo'})")

        self._client = Client(host, port, TcpProtocol)
        self._client.setConnectedCallback(self._on_connected)
        self._client.setDisconnectedCallback(self._on_disconnected)
        self._client.setMessageReceivedCallback(self._on_message)
        self._client.startService()

        reactor.run(installSignalHandlers=False)

    def _on_connected(self, client):
        from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAApplicationAuthReq
        logger.info("[cTrader] متصل — جاري مصادقة التطبيق...")
        self._connected = True

        req = ProtoOAApplicationAuthReq()
        req.clientId     = CLIENT_ID
        req.clientSecret = CLIENT_SECRET
        client.send(req)

    def _on_disconnected(self, client, reason):
        logger.warning(f"[cTrader] انقطع الاتصال — إعادة الاتصال خلال 10 ث...")
        self._connected  = False
        self._authorized = False
        if self._reactor:
            self._reactor.callLater(10, self._reconnect)

    def _reconnect(self):
        try:
            self._client.startService()
        except Exception as e:
            logger.error(f"[cTrader] فشل إعادة الاتصال: {e}")
            if self._reactor:
                self._reactor.callLater(30, self._reconnect)

    def _on_message(self, client, message):
        from ctrader_open_api.messages.OpenApiMessages_pb2 import (
            ProtoOAApplicationAuthRes, ProtoOAAccountAuthReq,
            ProtoOAAccountAuthRes, ProtoOATraderReq, ProtoOATraderRes,
            ProtoOAExecutionEvent, ProtoOAErrorRes, ProtoOASymbolsListReq,
            ProtoOASymbolsListRes,
        )
        from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoHeartbeatEvent
        from google.protobuf import descriptor

        try:
            pt = message.payloadType

            if pt == ProtoOAApplicationAuthRes().payloadType:
                logger.info("[cTrader] ✅ التطبيق مصادق عليه")
                req = ProtoOAAccountAuthReq()
                req.ctidTraderAccountId = ACCOUNT_ID
                req.accessToken         = ACCESS_TOKEN
                client.send(req)

            elif pt == ProtoOAAccountAuthRes().payloadType:
                logger.info(f"[cTrader] ✅ الحساب {ACCOUNT_ID} مصادق عليه")
                self._authorized = True
                self._last_ok    = time.monotonic()
                # جلب معلومات الحساب
                req = ProtoOATraderReq()
                req.ctidTraderAccountId = ACCOUNT_ID
                client.send(req)
                # جلب قائمة الرموز
                sreq = ProtoOASymbolsListReq()
                sreq.ctidTraderAccountId = ACCOUNT_ID
                sreq.includeArchivedSymbols = False
                client.send(sreq)

            elif pt == ProtoOATraderRes().payloadType:
                res = self._extract(message, ProtoOATraderRes)
                if res and hasattr(res, 'trader'):
                    self._balance     = res.trader.balance / 100.0
                    self._free_margin = getattr(res.trader, 'freeMargin', res.trader.balance) / 100.0
                    self._last_ok     = time.monotonic()
                    logger.info(f"[cTrader] 💰 الرصيد: ${self._balance:,.2f}")
                    # أعلم BalanceManager بالرصيد الحقيقي
                    if self._balance_mgr is not None:
                        self._balance_mgr.update_from_open_api(self._balance, self._free_margin)

            elif pt == ProtoOASymbolsListRes().payloadType:
                res = self._extract(message, ProtoOASymbolsListRes)
                if res:
                    for sym in res.symbol:
                        self._symbol_map[sym.symbolName.upper()] = sym.symbolId
                    xau_id = self._symbol_map.get("XAUUSD", "?")
                    logger.info(f"[cTrader] 📊 {len(self._symbol_map)} رمز | XAUUSD ID={xau_id}")

            elif pt == ProtoOAExecutionEvent().payloadType:
                res = self._extract(message, ProtoOAExecutionEvent)
                if res and hasattr(res, 'position') and res.position.positionId:
                    pos = res.position
                    self._positions[pos.positionId] = {
                        "positionId": pos.positionId,
                        "tradeType":  "BUY" if pos.tradeData.tradeSide == 1 else "SELL",
                        "volume":     pos.tradeData.volume / 100.0,
                        "entryPrice": pos.price,
                        "stopLoss":   pos.stopLoss,
                        "takeProfit": pos.takeProfit,
                    }
                    self._executed  += 1
                    self._last_ok    = time.monotonic()
                    logger.info(f"[cTrader] ✅ صفقة منفذة | ID={pos.positionId}")

            elif pt == ProtoOAErrorRes().payloadType:
                res = self._extract(message, ProtoOAErrorRes)
                if res:
                    logger.error(f"[cTrader] ❌ {res.errorCode}: {res.description}")

            elif pt == ProtoHeartbeatEvent().payloadType:
                self._last_ok = time.monotonic()

        except Exception as e:
            logger.error(f"[cTrader] خطأ في معالجة رسالة: {e}", exc_info=True)

    def _extract(self, message, klass):
        """استخراج رسالة Protobuf من ProtoMessage."""
        try:
            obj = klass()
            obj.ParseFromString(message.payload)
            return obj
        except Exception:
            return None

    def _send(self, proto_msg):
        """إرسال رسالة Protobuf عبر Twisted thread."""
        if self._client and self._reactor and self._connected:
            self._reactor.callFromThread(self._client.send, proto_msg)

    # ──────────────────────────────────────────────────────
    # PUBLIC INTERFACE
    # ──────────────────────────────────────────────────────

    async def execute_signal(self, signal) -> dict:
        if not self.connected:
            return {"ok": False, "error": "غير متصل بـ cTrader Open API"}

        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOANewOrderReq, ProtoOAOrderType, ProtoOATradeSide
            )

            sig = signal if isinstance(signal, dict) else signal.to_dict()
            sig_type = sig.get("signal_type", "BUY").upper()
            lot_size = float(sig.get("lot_size", 0.01))
            sl       = float(sig.get("stop_loss", 0))
            tp       = float(sig.get("take_profit", 0))

            symbol_id = self._symbol_map.get("XAUUSD", 0)
            if not symbol_id:
                return {"ok": False, "error": "لم يُعثر على XAUUSD في قائمة الرموز"}

            req = ProtoOANewOrderReq()
            req.ctidTraderAccountId = ACCOUNT_ID
            req.symbolId            = symbol_id
            req.orderType           = 1  # MARKET
            req.tradeSide           = 1 if sig_type == "BUY" else 2
            req.volume              = int(lot_size * 100000)  # micro lots
            if sl > 0:  req.stopLoss   = sl
            if tp > 0:  req.takeProfit = tp

            self._send(req)
            logger.info(f"[cTrader] 📤 {sig_type} | لوت={lot_size} | SL={sl} | TP={tp}")
            return {"ok": True, "status": "sent"}

        except Exception as e:
            self._rejected += 1
            logger.error(f"[cTrader] خطأ تنفيذ الإشارة: {e}")
            return {"ok": False, "error": str(e)}

    async def modify_position_sl(self, modification) -> dict:
        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAAmendPositionSLTPReq
            mod = modification if isinstance(modification, dict) else vars(modification)
            req = ProtoOAAmendPositionSLTPReq()
            req.ctidTraderAccountId = ACCOUNT_ID
            req.positionId          = mod.get("position_id", 0)
            req.stopLoss            = mod.get("new_sl", 0)
            self._send(req)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def get_account_info(self) -> dict:
        if self._reactor and self._client and self._connected:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOATraderReq
            req = ProtoOATraderReq()
            req.ctidTraderAccountId = ACCOUNT_ID
            self._send(req)
            await asyncio.sleep(0.5)
        return {
            "equity":        self._balance,
            "free_margin":   self._free_margin,
            "mcp_connected": self.connected,
            "source":        "cTrader Open API",
        }

    async def get_positions(self) -> list:
        return list(self._positions.values())

    def stats(self) -> dict:
        return {
            "mcp_connected":  self.connected,
            "executed":       self._executed,
            "rejected":       self._rejected,
            "open_positions": len(self._positions),
        }

    async def run_forever(self):
        """يشغّل Twisted في thread منفصل ويبقى حياً."""
        if not ACCESS_TOKEN:
            logger.error("[cTrader] CTRADER_ACCESS_TOKEN غير موجود — Open API معطّل")
            return
        if not CLIENT_ID or not CLIENT_SECRET:
            logger.error("[cTrader] CLIENT_ID أو CLIENT_SECRET غير موجود")
            return

        logger.info(f"[cTrader] بدء Open API | حساب={ACCOUNT_ID} | {'Live' if IS_LIVE else 'Demo'}")

        self._thread = threading.Thread(target=self._start_twisted, daemon=True)
        self._thread.start()

        # انتظر الاتصال
        for _ in range(50):
            if self.connected:
                break
            await asyncio.sleep(0.2)

        if self.connected:
            logger.info(f"[cTrader] ✅ متصل ومصادق | رصيد=${self._balance:,.2f}")
        else:
            logger.warning("[cTrader] ⚠️ لم يكتمل الاتصال بعد 10 ثوانٍ — سيستمر المحاولة")

        # بقاء حي
        while True:
            await asyncio.sleep(60)
            if not self.connected:
                logger.warning("[cTrader] غير متصل — في انتظار إعادة الاتصال...")
            else:
                age = time.monotonic() - self._last_ok
                logger.info(f"[cTrader] ✅ متصل | رصيد=${self._balance:,.2f} | آخر ping منذ {age:.0f}ث")
