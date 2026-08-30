const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

type ApiOptions = {
  method?: string;
  body?: unknown;
  token?: string | null;
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(
  path: string,
  { method = "GET", body, token }: ApiOptions = {}
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!res.ok) {
    let message = `request failed (${res.status})`;
    try {
      const data = await res.json();
      if (data?.detail) message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* ignore */
    }
    if (res.status === 401) {
      if (typeof window !== "undefined") window.location.href = "/login";
    }
    throw new ApiError(res.status, message);
  }
  const text = await res.text();
  return text ? (JSON.parse(text) as T) : ({} as T);
}

// Token storage: sessionStorage only (tab-scoped, cleared when the tab closes),
// backed by a module-level variable as a fallback so the token survives even if
// storage is unavailable. NEVER persisted to localStorage - XSS-safe by design.
const TOKEN_KEY = "fxscalper_token";
const EMAIL_KEY = "fxscalper_email";

let memoryToken: string | null = null;
let memoryEmail: string | null = null;

function storageGet(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function storageSet(key: string, value: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    /* private-mode storage may be unavailable; in-memory copy still holds it */
  }
}

function storageRemove(key: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

export const tokenStore = {
  get: (): string | null => {
    if (memoryToken !== null) return memoryToken;
    const stored = storageGet(TOKEN_KEY);
    if (stored) memoryToken = stored;
    return stored;
  },
  set: (token: string) => {
    memoryToken = token;
    storageSet(TOKEN_KEY, token);
  },
  clear: () => {
    memoryToken = null;
    memoryEmail = null;
    storageRemove(TOKEN_KEY);
    storageRemove(EMAIL_KEY);
  },
  getEmail: (): string | null => {
    if (memoryEmail !== null) return memoryEmail;
    const stored = storageGet(EMAIL_KEY);
    if (stored) memoryEmail = stored;
    return stored;
  },
  setEmail: (email: string) => {
    memoryEmail = email;
    storageSet(EMAIL_KEY, email);
  },
};
// ---- Exness/MT5 provider connection helpers ----

export async function getExnessStatusCard(token: string): Promise<import("@/types").ProviderConnectionStatusCard> {
  return api<import("@/types").ProviderConnectionStatusCard>("/providers/exness-mt5/status", { token });
}

export async function testExnessConnection(
  token: string,
  body: {
    mode: "gateway" | "server_side";
    environment?: "demo" | "real";
    login?: string;
    password?: string;
    server?: string;
    gateway_url?: string;
    pairing_code?: string;
    device_name?: string;
    idempotency_key?: string;
  }
): Promise<import("@/types").ExnessCapabilityReport> {
  return api("/providers/exness-mt5/test-connection", { method: "POST", body, token });
}

export async function connectExness(
  token: string,
  body: {
    connection_mode: "server_side_mt5" | "mt5_gateway_agent" | "approved_bridge";
    display_name: string;
    environment?: "demo" | "real";
    login?: string;
    password?: string;
    server?: string;
    use_read_only?: boolean;
    gateway_url?: string;
    pairing_code?: string;
    device_name?: string;
    account_label?: string;
    read_only_capabilities?: string[];
    confirm_read_only?: boolean;
    idempotency_key?: string;
  }
): Promise<import("@/types").ExnessConnectOut> {
  return api("/providers/exness-mt5/connect", { method: "POST", body, token });
}

export async function issueExnessPairing(
  token: string,
  body: { gateway_url: string; device_name: string; pairing_code?: string; idempotency_key?: string }
): Promise<import("@/types").PairingTokenOut> {
  return api("/providers/exness-mt5/pair-gateway", { method: "POST", body, token });
}

export async function verifyExnessGateway(
  token: string,
  gatewayId: string,
  pairingToken: string
): Promise<{ gateway_id?: string; status?: string; detail?: string }> {
  const q = `?gateway_id=${encodeURIComponent(gatewayId)}&pairing_token=${encodeURIComponent(pairingToken)}`;
  return api(`/providers/exness-mt5/gateway/verify${q}`, { method: "POST", token });
}

export async function disconnectExness(
  token: string,
  body: { connection_id?: string; keep_instruments?: boolean }
): Promise<{ disconnected: boolean; detail?: string }> {
  return api("/providers/exness-mt5/disconnect", { method: "POST", body, token });
}

export async function getExnessInstruments(
  token: string,
  connectionId?: string
): Promise<import("@/types").InstrumentMappingView[]> {
  const q = connectionId ? `?connection_id=${encodeURIComponent(connectionId)}` : "";
  return api(`/providers/exness-mt5/instruments${q}`, { token });
}

// ---- Real Historical Data validation helpers ----

export async function previewValidation(
  token: string,
  body: {
    connection_id: string;
    strategy_id: string;
    provider_symbol: string;
    canonical_symbol: string;
    timeout: string;
    start_time_utc?: string;
    end_time_utc?: string;
    days?: number;
  }
): Promise<import("@/types").ValidationPreview> {
  return api("/real-historical-validations/preview", { method: "POST", body, token });
}

export async function createValidationRun(
  token: string,
  body: {
    idempotency_key: string;
    connection_id: string;
    strategy_id: string;
    provider_symbol: string;
    canonical_symbol: string;
    timeout: string;
    start_time_utc?: string;
    end_time_utc?: string;
    days?: number;
    execution_model?: string;
    cost?: Record<string, unknown>;
  }
): Promise<import("@/types").ValidationRun> {
  return api("/real-historical-validations", { method: "POST", body, token });
}

export async function listValidationRuns(token: string, limit = 20): Promise<import("@/types").ValidationRun[]> {
  return api(`/real-historical-validations?limit=${limit}`, { token });
}

export async function getValidationRun(token: string, runId: string): Promise<import("@/types").ValidationRun> {
  return api(`/real-historical-validations/${runId}`, { token });
}

export async function getValidationCandles(
  token: string,
  runId: string,
  limit = 2000
): Promise<import("@/types").CandleResponse> {
  return api(`/real-historical-validations/${runId}/candles?limit=${limit}`, { token });
}

export async function getValidationTrades(token: string, runId: string): Promise<import("@/types").ValidationTrade[]> {
  return api(`/real-historical-validations/${runId}/trades`, { token });
}

export async function getValidationSignals(token: string, runId: string): Promise<import("@/types").ValidationSignal[]> {
  return api(`/real-historical-validations/${runId}/signals`, { token });
}

export async function getValidationMetrics(
  token: string,
  runId: string
): Promise<import("@/types").ValidationMetrics> {
  return api(`/real-historical-validations/${runId}/metrics`, { token });
}

export async function getValidationQuality(token: string, runId: string): Promise<import("@/types").ValidationQuality> {
  return api(`/real-historical-validations/${runId}/data-quality`, { token });
}

export async function getValidationEquity(
  token: string,
  runId: string
): Promise<{ equity_curve: { ts: number; balance: number }[] }> {
  return api(`/real-historical-validations/${runId}/equity-curve`, { token });
}

export async function cancelValidationRun(token: string, runId: string): Promise<{ error_safe?: string }> {
  return api(`/real-historical-validations/${runId}/cancel`, { method: "POST", token });
}

export async function exportValidationRun(
  token: string,
  runId: string,
  body: { format?: "json" | "csv" }
): Promise<Record<string, unknown>> {
  return api(`/real-historical-validations/${runId}/export`, { method: "POST", body, token });
}

export async function getStrategies(token: string): Promise<import("@/types").Strategy[]> {
  return api("/strategies", { token });
}

// -- AI Strategy Analyzer ---------------------------------------------------
export async function analyzeStrategy(
  token: string,
  prompt_text: string
): Promise<import("@/types").StrategyAnalysis> {
  return api("/strategy-analyzer/analyze", { method: "POST", body: { prompt_text }, token });
}

// -- Real Backtests (AI Strategy Tester data source) ------------------------
export async function realBacktestPreview(
  token: string,
  body: {
    strategy_id: string;
    strategy_version_id?: string;
    connection_id?: string;
    provider: string;
    provider_symbol: string;
    timeout: string;
    start_time_utc: string;
    end_time_utc: string;
  }
): Promise<import("@/types").ValidationPreview> {
  return api("/real-backtests/preview", { method: "POST", body, token });
}

export async function createRealBacktest(
  token: string,
  body: Record<string, unknown>
): Promise<import("@/types").ValidationRun> {
  return api("/real-backtests", { method: "POST", body, token });
}

export async function listRealBacktests(token: string, limit = 20): Promise<import("@/types").ValidationRun[]> {
  return api(`/real-backtests?limit=${limit}`, { token });
}

export async function getRealBacktest(token: string, runId: string): Promise<import("@/types").ValidationRun> {
  return api(`/real-backtests/${runId}`, { token });
}

export async function getRealBacktestChart(
  token: string,
  runId: string
): Promise<import("@/types").RealBacktestChart> {
  return api(`/real-backtests/${runId}/chart`, { token });
}

export async function getRealBacktestMetrics(
  token: string,
  runId: string
): Promise<import("@/types").ValidationMetrics> {
  return api(`/real-backtests/${runId}/metrics`, { token });
}

export async function saveStrategy(
  token: string,
  body: { name: string; spec: unknown; notes?: string }
): Promise<{ id: string; name: string; current_version: string; status: string }> {
  return api("/strategies", { method: "POST", body, token });
}

export async function addStrategyVersion(
  token: string,
  strategyId: string,
  body: { spec: unknown; notes?: string }
): Promise<{ version: string; notes: string; created_at: string }> {
  return api(`/strategies/${strategyId}/versions`, { method: "POST", body, token });
}

export async function getStrategyVersions(
  token: string,
  strategyId: string
): Promise<{ id: string; version: string; notes: string; created_at: string }[]> {
  return api(`/strategies/${strategyId}/versions`, { token });
}
