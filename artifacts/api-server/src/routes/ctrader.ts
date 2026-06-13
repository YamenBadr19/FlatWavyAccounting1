import { Router, type IRouter } from "express";
import {
  fetchAccounts,
  fetchPositions,
  fetchDeals,
  CtraderApiError,
} from "../lib/ctrader";
import {
  ListCtraderAccountsResponse,
  GetCtraderPositionsResponse,
  GetCtraderDealsResponse,
  GetCtraderDealsQueryParams,
} from "@workspace/api-zod";

const router: IRouter = Router();

router.get("/ctrader/accounts", async (req, res): Promise<void> => {
  try {
    const raw = await fetchAccounts();
    const accounts = raw.map((a) => ({
      accountId: String(a.accountId),
      traderLogin: a.accountNumber,
      accountType: a.traderAccountType,
      balance: a.balance / 100,
      currency: a.depositCurrency,
      leverageInCents: a.leverageInCents,
      brokerName: a.brokerTitle,
      isLive: a.live,
    }));
    res.json(ListCtraderAccountsResponse.parse({ accounts }));
  } catch (err) {
    if (err instanceof CtraderApiError) {
      req.log.error({ status: err.statusCode }, "cTrader accounts error");
      res.status(502).json({ error: `cTrader API error: ${err.message}` });
      return;
    }
    throw err;
  }
});

router.get(
  "/ctrader/accounts/:accountId/positions",
  async (req, res): Promise<void> => {
    const raw = Array.isArray(req.params.accountId)
      ? req.params.accountId[0]
      : req.params.accountId;

    if (!raw) {
      res.status(400).json({ error: "accountId is required" });
      return;
    }

    try {
      const rawPositions = await fetchPositions(raw);
      const positions = rawPositions.map((p) => ({
        positionId: String(p.positionId),
        symbol: p.symbolName,
        tradeSide: p.tradeSide,
        volume: p.volume / 100,
        entryPrice: p.entryPrice / 100000,
        currentPrice: (p.currentPrice ?? 0) / 100000,
        pnl: (p.unrealizedGrossPnl ?? 0) / 100,
        openTimestamp: p.openTimestamp,
      }));
      res.json(GetCtraderPositionsResponse.parse({ positions }));
    } catch (err) {
      if (err instanceof CtraderApiError) {
        req.log.error({ status: err.statusCode }, "cTrader positions error");
        res.status(502).json({ error: `cTrader API error: ${err.message}` });
        return;
      }
      throw err;
    }
  },
);

router.get(
  "/ctrader/accounts/:accountId/deals",
  async (req, res): Promise<void> => {
    const raw = Array.isArray(req.params.accountId)
      ? req.params.accountId[0]
      : req.params.accountId;

    if (!raw) {
      res.status(400).json({ error: "accountId is required" });
      return;
    }

    const params = GetCtraderDealsQueryParams.safeParse(req.query);
    if (!params.success) {
      res.status(400).json({ error: params.error.message });
      return;
    }

    try {
      const rawDeals = await fetchDeals(raw, params.data.from, params.data.to);
      const deals = rawDeals.map((d) => ({
        dealId: String(d.dealId),
        symbol: d.symbolName,
        tradeSide: d.tradeSide,
        volume: d.volume / 100,
        executionPrice: d.executionPrice / 100000,
        commission: (d.commission ?? 0) / 100,
        pnl: (d.grossProfit ?? 0) / 100,
        closeTimestamp: d.closeTimestamp,
      }));
      res.json(GetCtraderDealsResponse.parse({ deals }));
    } catch (err) {
      if (err instanceof CtraderApiError) {
        req.log.error({ status: err.statusCode }, "cTrader deals error");
        res.status(502).json({ error: `cTrader API error: ${err.message}` });
        return;
      }
      throw err;
    }
  },
);

export default router;
