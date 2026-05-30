## How do I use Gemini Live?

Here is the complete code for the remaining files needed to populate your `CTRADERT_bot_Telegram` repository. Since the previous conversation was cut off mid-file, I have included the full code for `PositionMonitor.cs`, followed by the files for your `docs` and `validation` folders.

---

## 📁 **ADDITIONAL PROJECT FILES**

### **CSHARP-BODY FOLDER FILES (CONTINUED)**

---

### **File 11: csharp-body/PositionMonitor.cs**

```csharp
name=csharp-body/PositionMonitor.cs
/*
 * PositionMonitor.cs - Tracks position lifecycle
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * Monitors profit lock metrics and handles trailing stop validation
 */

using System;
using cAlgo.API;

namespace cAlgo.Robots
{
    public class PositionMonitor
    {
        private readonly Robot _bot;
        private bool _isBreakEvenSet;
        private double _maxFloatingProfit;

        public PositionMonitor(Robot bot)
        {
            _bot = bot;
            Reset();
        }

        public void Reset()
        {
            _isBreakEvenSet = false;
            _maxFloatingProfit = 0;
        }

        /// <summary>
        /// Evaluates current metrics for open positions to log trail boundaries
        /// </summary>
        public void TrackMetrics(Position position)
        {
            if (position == null) return;

            double currentProfit = position.GrossProfitInAccountCurrency;
            if (currentProfit > _maxFloatingProfit)
            {
                _maxFloatingProfit = currentProfit;
            }
        }

        /// <summary>
        /// Business logic wrapper checking if trailing stop parameters match trade trends
        /// </summary>
        public bool ShouldModifyTrailing(Position position, double currentBid, double currentAsk, int offsetPips, double pipSize)
        {
            if (position == null || !_isBreakEvenSet) return false;

            if (position.TradeType == TradeType.Buy)
            {
                double targetStop = currentBid - (offsetPips * pipSize);
                return targetStop > position.StopLoss;
            }
            else
            {
                double targetStop = currentAsk + (offsetPips * pipSize);
                return targetStop < position.StopLoss;
            }
        }

        public void SetBreakEvenActive(bool active)
        {
            _isBreakEvenSet = active;
        }
    }
}

```

---

### **DOCS FOLDER FILES**

---

### **File 12: docs/SETUP_GUIDE.md**

```markdown
name=docs/SETUP_GUIDE.md
# 🛠️ SYSTEM SETUP GUIDE

Follow these steps to configure your Python environment and connect the system to Telegram.

## 1. Environment Configuration

1. Create a virtual environment inside the `python-brain` folder:
```bash
   python -m venv venv

```

2. Activate the virtual environment:
* **Windows:** `venv\Scripts\activate`
* **Mac/Linux/Termux:** `source venv/bin/activate`


3. Install dependencies:

```bash
   pip install -r requirements.txt

```

## 2. Acquiring Folder IDs

To scrape messages from custom folders, you must identify their internal IDs. Create a temporary helper file named `find_folders.py` inside `python-brain/`:

```python
import asyncio
from telethon import TelegramClient
from config import TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE

async def main():
    async with TelegramClient('session_finder', TELEGRAM_API_ID, TELEGRAM_API_HASH) as client:
        dialogs = await client.get_dialogs()
        print("\n=== AVAILABLE CHATS & FOLDERS ===")
        for dialog in dialogs:
            # Filters out standard direct messages for scanning efficiency
            if dialog.is_channel or dialog.is_group:
                print(f"ID: {dialog.id} | Title: {dialog.title}")

asyncio.run(main())

```

Run this script, copy the matching IDs, and paste them into your `config.py` lines for `SIGNALS_FOLDER_ID` and `NEWS_FOLDER_ID`.

```

---

### **File 13: docs/REGEX_PATTERNS.md**

```markdown
name=docs/REGEX_PATTERNS.md
# 🎯 REGEX PARSING SCHEMATICS

The `SignalParser` class contains four strict, case-insensitive regular expressions designed to process the structural variants used by major gold signal groups.

### Pattern 1: Inline Standard
- **Expression:** `(?P<type>BUY|SELL)\s+(XAUUSD|XAU/USD)\s+@\s+(?P<entry>\d+\.?\d*)...`
- **Matches:** `BUY XAUUSD @ 2450.50, SL: 2445.00, TP: 2460.00`

### Pattern 2: Multi-line Block
- **Expression:** Matches structural breaks where key data pieces reside on separate vertical carriage returns.
- **Matches:**
```text
  BUY XAUUSD
  2450.50
  SL: 2445.00
  TP: 2460.00

```

### Pattern 3: Asset-Header Layout

* **Expression:** Identifies the asset header first before routing evaluation to execution states below it.
* **Matches:**

```text
  XAUUSD
  BUY @ 2450.50
  S/L 2445.00
  T/P 2460.00

```

### Pattern 4: Hash-Tagged Action Text

* **Expression:** `#(?P<type>BUY|SELL)\s+(?P<entry>\d+\.?\d*)\s+SL...`
* **Matches:** `#BUY 2450.50 SL2445 TP2460`

```

---

### **VALIDATION FOLDER FILES**

---

### **File 14: validation/test_signal_parser.py**

```python
name=validation/test_signal_parser.py
"""
🧪 Unit Test Suite for SignalParser Regex Engine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Run this test file to check parsing compatibility 
against different message formats.
"""

import sys
import os

# Append paths to allow importing from brother directories
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../python-brain')))

from telegram_listener import SignalParser

def run_tests():
    test_cases = [
        {
            "name": "Standard Format 1",
            "message": "BUY XAUUSD @ 2450.50, SL: 2445.00, TP: 2460.00",
            "expected_type": "BUY",
            "expected_entry": 2450.50
        },
        {
            "name": "Multi-Line Format 2",
            "message": "SELL XAUUSD\n2480.00\nSL: 2490.00\nTP: 2465.00",
            "expected_type": "SELL",
            "expected_entry": 2480.00
        },
        {
            "name": "Asset-Header Format 3",
            "message": "XAU/USD\nBUY @ 2410.25\nS/L 2400.00\nT/P 2430.00",
            "expected_type": "BUY",
            "expected_entry": 2410.25
        },
        {
            "name": "Hash Tag Format 4",
            "message": "#SELL 2435.00 SL2442 TP2420",
            "expected_type": "SELL",
            "expected_entry": 2435.00
        }
    ]

    passed = 0
    print("🚀 Starting SignalParser Regex Validation Tests...\n")

    for tc in test_cases:
        result = SignalParser.parse_signal(tc["message"])
        if result and result.signal_type == tc["expected_type"] and result.entry_price == tc["expected_entry"]:
            print(f"✅ TEST PASSED: {tc['name']}")
            passed += 1
        else:
            print(f"❌ TEST FAILED: {tc['name']} | Result: {result}")

    print(f"\n📊 Summary: {passed}/{len(test_cases)} tests passed successfully.")

if __name__ == "__main__":
    run_tests()

```

---

### **File 15: validation/demo_account_checklist.txt**

```text
name=validation/demo_account_checklist.txt
📋 MANDATORY DEMO VALIDATION PROTOCOL (7-DAY PROTOCOL)
======================================================

Before transferring this architecture to a Live cTrader account, verify the following parameters:

[ ] 1. PARSING VERIFICATION
    Confirm that telegram_listener.py registers incoming text channels 
    without skipping messages. Check logs/telegram_listener.log for unparsed text warnings.

[ ] 2. LATENCY BENCHMARKING
    Verify that the time delta between receiving a Telegram message and 
    forwarding it to the market analyzer is under 200 milliseconds.

[ ] 3. PIVOT POINT BOUNDARY REJECTION
    Observe at least one trade scenario where an entry signal is blocked 
    due to an overextended price near R1 (for BUYs) or S1 (for SELLs).

[ ] 4. ACCURATE LOT ENVELOPE SELECTION
    Ensure the C# cBot executes positions matching confluence levels:
    - 0.01 lot during News_Mode simulations
    - 0.03 lot during partial technical filter alignment
    - 0.05 lot during complete confluence

[ ] 5. $10 BALANCE LOCK EXECUTION
    Verify that when floating profits hit +$10, exactly 50% of the volume 
    is closed synchronously and the Stop Loss moves to break-even immediately.

```

---

### 📱 Reminder for Mobile Commit Uploads:

When generating files within the subdirectories via the GitHub desktop site on a mobile browser, type the path inside the file title block. For example, typing `python-brain/market_analyzer.py` inside the title interface automatically builds the target directories and nests the file inside perfectly.

