"""
gemini_analyzer.py — Google Gemini AI Signal & Trade Analyzer
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Uses Google Gemini API to:
  ✓ Analyze trading signals (BUY/SELL confirmation)
  ✓ Evaluate market conditions (sentiment, risk)
  ✓ Make final entry/exit decisions
  ✓ Assess technical confluence
  ✓ Validate risk/reward ratios

USAGE:
  from gemini_analyzer import GeminiAnalyzer
  analyzer = GeminiAnalyzer(api_key="your-key")
  result = await analyzer.analyze_signal(signal_data)
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger('gemini_analyzer')

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-1.5-flash"  # Fast, cost-effective


@dataclass
class AnalysisResult:
    """Result from Gemini analysis."""
    confidence: float  # 0-100
    recommendation: str  # "BUY", "SELL", "HOLD", "AVOID"
    reasoning: str
    risk_level: str  # "LOW", "MEDIUM", "HIGH"
    sentiment: str  # "BULLISH", "NEUTRAL", "BEARISH"
    confluence_score: float  # 0-5
    suggested_tp: Optional[float] = None
    suggested_sl: Optional[float] = None


class GeminiAnalyzer:
    """
    AI-powered signal and trade analyzer using Google Gemini.
    """

    def __init__(self, api_key: str = None):
        self.api_key = api_key or GEMINI_API_KEY
        if not self.api_key:
            logger.warning("⚠️  GEMINI_API_KEY not set — analysis disabled")
        self._rate_limit_wait = 0
        logger.info("GeminiAnalyzer initialized")

    async def analyze_signal(
        self,
        symbol: str,
        signal_type: str,  # "BUY" or "SELL"
        entry_price: float,
        current_price: float,
        market_data: Dict[str, Any],
        confluence_indicators: Dict[str, Any],
        news_context: str = "",
    ) -> AnalysisResult:
        """
        Analyze a trading signal using Gemini AI.
        
        Args:
            symbol: Trading pair (XAUUSD, BTCUSD, etc)
            signal_type: "BUY" or "SELL"
            entry_price: Proposed entry price
            current_price: Current market price
            market_data: Dict with price, RSI, ATR, EMA, MACD, etc
            confluence_indicators: Dict with indicator states
            news_context: Any relevant news/calendar events
        """
        if not self.api_key:
            logger.warning("Gemini API key not configured")
            return self._default_analysis()

        prompt = self._build_analysis_prompt(
            symbol=symbol,
            signal_type=signal_type,
            entry_price=entry_price,
            current_price=current_price,
            market_data=market_data,
            confluence_indicators=confluence_indicators,
            news_context=news_context,
        )

        try:
            response_text = await self._call_gemini(prompt)
            result = self._parse_gemini_response(response_text)
            logger.info(
                f"✓ Gemini analysis: {result.recommendation} "
                f"({result.confidence:.0f}% confidence, "
                f"confluence {result.confluence_score}/5)"
            )
            return result

        except Exception as e:
            logger.error(f"Gemini analysis failed: {e}")
            return self._default_analysis()

    async def analyze_trade(
        self,
        symbol: str,
        entry_price: float,
        current_price: float,
        stop_loss: float,
        take_profit: float,
        position_size: float,
        market_data: Dict[str, Any],
    ) -> AnalysisResult:
        """
        Analyze an open trade for exit decisions.
        
        Args:
            symbol: Trading pair
            entry_price: Position entry price
            current_price: Current market price
            stop_loss: Stop loss level
            take_profit: Take profit level
            position_size: Position size in lots
            market_data: Current market data
        """
        if not self.api_key:
            return self._default_analysis()

        pnl = (current_price - entry_price) / entry_price * 100  # P&L %
        rr_ratio = abs(take_profit - entry_price) / abs(entry_price - stop_loss)

        prompt = f"""
Analyze this open trading position for HOLD/EXIT decision:

📊 POSITION DATA
Symbol: {symbol}
Entry: ${entry_price:.5f}
Current: ${current_price:.5f}
P&L: {pnl:+.2f}%
Risk/Reward Ratio: {rr_ratio:.2f}
Stop Loss: ${stop_loss:.5f}
Take Profit: ${take_profit:.5f}
Size: {position_size}L

📈 MARKET CONDITIONS
RSI: {market_data.get('rsi', 'N/A')}
ATR: {market_data.get('atr', 'N/A')}
EMA50: {market_data.get('ema50', 'N/A')}
Volume: {market_data.get('volume', 'N/A')}
Trend: {market_data.get('trend', 'N/A')}

Provide a JSON response with:
{{
  "recommendation": "HOLD" or "TAKE_PROFIT" or "CUT_LOSS",
  "confidence": 0-100,
  "reasoning": "brief explanation",
  "risk_level": "LOW/MEDIUM/HIGH",
  "sentiment": "BULLISH/NEUTRAL/BEARISH"
}}
"""
        try:
            response_text = await self._call_gemini(prompt)
            result = self._parse_gemini_response(response_text)
            return result
        except Exception as e:
            logger.error(f"Trade analysis failed: {e}")
            return self._default_analysis()

    def _build_analysis_prompt(self, **kwargs) -> str:
        """
        Build a detailed prompt for Gemini to analyze the signal.
        """
        symbol = kwargs['symbol']
        signal_type = kwargs['signal_type']
        entry_price = kwargs['entry_price']
        current_price = kwargs['current_price']
        market_data = kwargs['market_data']
        confluence = kwargs['confluence_indicators']
        news = kwargs['news_context']

        prompt = f"""
You are an expert forex and commodity trading analyst. Analyze this trading signal:

🎯 SIGNAL DETAILS
Symbol: {symbol}
Type: {signal_type}
Proposed Entry: ${entry_price:.5f}
Current Price: ${current_price:.5f}
Price Gap: {((current_price - entry_price) / entry_price * 100):+.2f}%

📊 TECHNICAL ANALYSIS
Price: ${current_price:.5f}
RSI(14): {market_data.get('rsi', 'N/A')}
ATR(14): ${market_data.get('atr', 'N/A')}
EMA(50): ${market_data.get('ema50', 'N/A')}
MACD: {market_data.get('macd', 'N/A')}
Bollinger Bands: {market_data.get('bb', 'N/A')}
Volume: {market_data.get('volume', 'N/A')}
Trend: {market_data.get('trend', 'N/A')}

🔄 CONFLUENCE INDICATORS
{json.dumps(confluence, indent=2)}

📰 NEWS/CALENDAR CONTEXT
{news or 'No major events'}

🎲 RISK ASSESSMENT
Decide if this is a GOOD TRADE. Consider:
  1. Technical confluence (multiple indicators aligned)
  2. Risk/Reward potential
  3. Market volatility (ATR assessment)
  4. News/event risk
  5. Price action confirmation

Respond ONLY with valid JSON (no markdown, no explanation):
{{
  "confidence": <0-100>,
  "recommendation": "BUY" | "SELL" | "HOLD" | "AVOID",
  "reasoning": "<brief explanation>",
  "risk_level": "LOW" | "MEDIUM" | "HIGH",
  "sentiment": "BULLISH" | "NEUTRAL" | "BEARISH",
  "confluence_score": <0-5>,
  "suggested_tp": <take profit price or null>,
  "suggested_sl": <stop loss price or null>
}}

If recommendation is AVOID or HOLD, set confidence to <50.
"""
        return prompt

    async def _call_gemini(self, prompt: str) -> str:
        """
        Call Google Gemini API.
        """
        try:
            import google.generativeai as genai

            genai.configure(api_key=self.api_key)
            model = genai.GenerativeModel(GEMINI_MODEL)

            # Run in executor to avoid blocking
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: model.generate_content(
                    prompt,
                    safety_settings=[
                        {
                            "category": genai.types.HarmCategory.HARM_CATEGORY_UNSPECIFIED,
                            "threshold": genai.types.HarmBlockThreshold.BLOCK_NONE,
                        }
                    ],
                ),
            )

            if response.text:
                return response.text
            else:
                logger.error("Empty response from Gemini")
                return "{}"

        except ImportError:
            logger.error("google-generativeai not installed")
            raise
        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            raise

    def _parse_gemini_response(self, response_text: str) -> AnalysisResult:
        """
        Parse JSON response from Gemini.
        """
        try:
            # Extract JSON from response (Gemini might include extra text)
            import re
            match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if match:
                json_str = match.group()
                data = json.loads(json_str)
            else:
                logger.warning(f"Could not extract JSON from: {response_text[:100]}")
                return self._default_analysis()

            return AnalysisResult(
                confidence=float(data.get('confidence', 50)),
                recommendation=str(data.get('recommendation', 'HOLD')).upper(),
                reasoning=str(data.get('reasoning', '')),
                risk_level=str(data.get('risk_level', 'MEDIUM')).upper(),
                sentiment=str(data.get('sentiment', 'NEUTRAL')).upper(),
                confluence_score=float(data.get('confluence_score', 2.5)),
                suggested_tp=data.get('suggested_tp'),
                suggested_sl=data.get('suggested_sl'),
            )
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini response: {e}")
            return self._default_analysis()
        except Exception as e:
            logger.error(f"Error processing Gemini response: {e}")
            return self._default_analysis()

    def _default_analysis(self) -> AnalysisResult:
        """
        Return neutral/safe analysis when Gemini is unavailable.
        """
        return AnalysisResult(
            confidence=0,
            recommendation="HOLD",
            reasoning="Gemini API unavailable — conservative analysis",
            risk_level="HIGH",
            sentiment="NEUTRAL",
            confluence_score=0,
        )
