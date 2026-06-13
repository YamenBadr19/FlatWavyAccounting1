import { logger } from "./logger";

const CTRADER_BASE_URL = "https://api.spotware.com/connect";
const CTRADER_AUTH_URL = "https://connect.spotware.com/apps/token";

// In-memory token store — refreshed automatically on expiry
let cachedAccessToken: string | null = null;
let tokenExpiresAt: number = 0; // Unix ms

async function refreshAccessToken(): Promise<string> {
  const refreshToken = process.env["CTRADER_REFRESH_TOKEN"];
  const clientId = process.env["CTRADER_CLIENT_ID"];
  const clientSecret = process.env["CTRADER_CLIENT_SECRET"];

  if (!refreshToken || !clientId || !clientSecret) {
    throw new Error(
      "CTRADER_REFRESH_TOKEN, CTRADER_CLIENT_ID, and CTRADER_CLIENT_SECRET must all be set",
    );
  }

  logger.info("Refreshing cTrader access token");

  const body = new URLSearchParams({
    grant_type: "refresh_token",
    refresh_token: refreshToken,
    client_id: clientId,
    client_secret: clientSecret,
  });

  const res = await fetch(CTRADER_AUTH_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    logger.error({ status: res.status, text }, "Failed to refresh cTrader token");
    throw new CtraderApiError(res.status, `Token refresh failed: ${text}`);
  }

  const json = (await res.json()) as {
    access_token: string;
    expires_in?: number;
    refresh_token?: string;
  };

  cachedAccessToken = json.access_token;
  // Expire 60 seconds early to avoid edge cases
  const expiresIn = json.expires_in ?? 3600;
  tokenExpiresAt = Date.now() + (expiresIn - 60) * 1000;

  logger.info({ expiresIn }, "cTrader access token refreshed");
  return cachedAccessToken;
}

async function getAccessToken(): Promise<string> {
  // Use cached token if still valid
  if (cachedAccessToken && Date.now() < tokenExpiresAt) {
    return cachedAccessToken;
  }

  // On first call, use env token directly (do NOT refresh immediately)
  if (!cachedAccessToken) {
    const envToken = process.env["CTRADER_ACCESS_TOKEN"];
    if (envToken) {
      cachedAccessToken = envToken;
      // Mark it as expiring in 5 min — short enough to trigger refresh soon
      // but long enough to attempt the first request with it
      tokenExpiresAt = Date.now() + 5 * 60 * 1000;
      return envToken;
    }
  }

  return refreshAccessToken();
}

async function ctraderFetch<T>(path: string): Promise<T> {
  const token = await getAccessToken();
  const separator = path.includes("?") ? "&" : "?";
  const url = `${CTRADER_BASE_URL}${path}${separator}oauth_token=${encodeURIComponent(token)}`;

  logger.debug({ path }, "cTrader API request");

  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
  });

  // If token was rejected, force a refresh and retry once
  if (res.status === 401 || res.status === 403) {
    logger.warn("cTrader token rejected, forcing refresh and retrying");
    cachedAccessToken = null;
    tokenExpiresAt = 0;

    const freshToken = await refreshAccessToken();
    const separator2 = path.includes("?") ? "&" : "?";
    const retryUrl = `${CTRADER_BASE_URL}${path}${separator2}oauth_token=${encodeURIComponent(freshToken)}`;
    const retryRes = await fetch(retryUrl, {
      headers: { "Content-Type": "application/json" },
    });

    if (!retryRes.ok) {
      const body = await retryRes.text().catch(() => "");
      logger.error({ status: retryRes.status, body }, "cTrader API error after retry");
      throw new CtraderApiError(retryRes.status, body);
    }

    return retryRes.json() as Promise<T>;
  }

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    logger.error({ status: res.status, body }, "cTrader API error");
    throw new CtraderApiError(res.status, body);
  }

  return res.json() as Promise<T>;
}

export class CtraderApiError extends Error {
  constructor(
    public readonly statusCode: number,
    message: string,
  ) {
    super(message);
    this.name = "CtraderApiError";
  }
}

export interface RawCtraderAccount {
  accountId: number;
  accountNumber: number;
  traderAccountType: string;
  balance: number;
  depositCurrency: string;
  leverageInCents: number;
  brokerTitle: string;
  live: boolean;
}

export interface RawCtraderPosition {
  positionId: number;
  symbolName: string;
  tradeSide: string;
  volume: number;
  entryPrice: number;
  currentPrice?: number;
  unrealizedGrossPnl?: number;
  openTimestamp: number;
}

export interface RawCtraderDeal {
  dealId: number;
  symbolName: string;
  tradeSide: string;
  volume: number;
  executionPrice: number;
  commission: number;
  grossProfit?: number;
  closeTimestamp: number;
}

export async function fetchAccounts(): Promise<RawCtraderAccount[]> {
  const data = await ctraderFetch<{ data: RawCtraderAccount[] }>(
    "/tradingaccounts",
  );
  return data.data ?? [];
}

export async function fetchPositions(
  accountId: string,
): Promise<RawCtraderPosition[]> {
  const data = await ctraderFetch<{ data: RawCtraderPosition[] }>(
    `/tradingaccounts/${accountId}/positions`,
  );
  return data.data ?? [];
}

export async function fetchDeals(
  accountId: string,
  from?: number,
  to?: number,
): Promise<RawCtraderDeal[]> {
  const params = new URLSearchParams();
  if (from != null) params.set("from", String(from));
  if (to != null) params.set("to", String(to));

  const query = params.toString() ? `?${params.toString()}` : "";
  const data = await ctraderFetch<{ data: RawCtraderDeal[] }>(
    `/tradingaccounts/${accountId}/deals${query}`,
  );
  return data.data ?? [];
}
