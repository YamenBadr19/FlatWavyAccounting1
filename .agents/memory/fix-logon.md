---
name: FIX Logon Rejection
description: Broker disconnects at TCP level before sending FIX-level reject; debugging approach.
---

## Observation
cTrader FIX broker (live-uk-eqx-01.p.c-trader.com / demo-uk-eqx-01.p.c-trader.com port 5212) 
closes the TCP connection silently after receiving the Logon (msg type A). The `_dispatch()` handler 
never sees a msg type 3 (Session Reject) with Tag 58, because no FIX message is sent before disconnect.

Logs show: "TCP connected (TLS)" → "Logon sent" → "Reconnect in Xs" — no intervening message.

**Why:** The broker likely validates credentials at the TCP/TLS handshake level or drops the connection 
without a proper FIX-level response when credentials are wrong. This is common with cTrader FIX.

**How to apply:**
- If credentials change, look for "Logged in ✓" in logs — that's the success indicator.
- If still reconnecting in a loop, contact broker support with SenderCompID and ask for FIX credential verification.
- The exponential backoff (5→10→20→40→80→120s cap) is working correctly — brain stays alive.
- Tag58 parsing code IS correct and will work once broker sends proper FIX Reject messages.
