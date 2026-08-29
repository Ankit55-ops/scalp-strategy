export type Spec = {
  name: string;
  version: string;
  strategy_family: string;
  supported_pairs: string[];
  supported_timeframes: string[];
  sessions_utc: { name: string; start: string; end: string }[];
  market_regime: { preferred: string[]; avoid: string[] };
  indicators: { name: string; parameters: Record<string, number> }[];
  entry_rules: { id: string; description: string; expression: string }[];
  exit_rules: { id: string; description: string; expression: string }[];
  risk_management: {
    risk_per_trade_pct: number;
    max_daily_loss_pct: number;
    max_consecutive_losses: number;
    max_open_positions: number;
    max_trades_per_day: number;
    stop_loss_method: string;
    stop_loss_parameters: Record<string, number>;
    take_profit_method: string;
    take_profit_parameters: Record<string, number>;
  };
  execution_filters: {
    max_spread_pips: number;
    max_slippage_pips: number;
    minimum_atr_pips: number;
    news_blackout_minutes_before: number;
    news_blackout_minutes_after: number;
  };
  assumptions: string[];
  failure_modes: string[];
  plain_english_explanation: string;
  confidence_notes: string;
};

export type Strategy = {
  id: string;
  name: string;
  strategy_family: string;
  current_version: string;
  status: string;
  created_at: string;
};

export type BacktestSummary = {
  job_id: string;
  status: string;
  metrics: Record<string, number | string | null>;
  validation: { classification?: { status?: string; score?: number } } | null;
  robustness: Record<string, unknown> | null;
  starting_balance: number;
  ending_balance: number;
};

export type Trade = {
  symbol: string;
  side: string;
  entry_ts: number;
  exit_ts: number;
  entry_price: number;
  exit_price: number;
  size_units: number;
  stop: number;
  target: number;
  net_pnl: number;
  pips: number;
  exit_reason: string;
  spread_cost: number;
  slippage_cost: number;
  commission: number;
};

export type RiskProfile = {
  id: string;
  name: string;
  risk_per_trade_pct: number;
  max_daily_loss_pct: number;
  max_weekly_loss_pct: number;
  max_drawdown_pct: number;
  max_consecutive_losses: number;
  max_open_positions: number;
  max_trades_per_day: number;
  max_correlated_exposure_pct: number;
  max_spread_pips: number;
  max_slippage_pips: number;
  news_blackout_minutes_before: number;
  news_blackout_minutes_after: number;
  hard_stop_distance_pips: number;
  is_active: boolean;
  created_at: string;
};

export type PaperStatus = {
  is_active: boolean;
  balance: number;
  equity: number;
  open_positions: number;
  closed_trades: number;
  trading_state: string;
  state_reason: string | null;
  pending_orders: number;
};

export type PaperPosition = {
  id: string;
  order_id: string | null;
  symbol: string;
  side: string;
  size_units: number;
  entry_price: number;
  mark_price: number;
  stop_loss: number;
  take_profit: number;
  open_ts: number;
  unrealized_pnl: number;
};

export type PaperTrade = {
  id: string;
  symbol: string;
  side: string;
  entry_ts: number;
  exit_ts: number;
  entry_price: number;
  exit_price: number;
  stop_loss: number;
  take_profit: number;
  size_units: number;
  pips: number;
  gross_pnl: number;
  net_pnl: number;
  commission: number;
  exit_reason: string;
};

export type Broker = {
  id: string;
  provider: string;
  label: string;
  status: string;
  is_sandbox: boolean;
};

export type Deployment = {
  id: string;
  strategy_id: string;
  strategy_name: string;
  broker_connection_id: string | null;
  broker_label: string;
  status: string;
  checks: {
    paper_track_record?: { passed: boolean; closed_trades?: number; required_min_trades?: number; note?: string };
    risk_profile?: { passed: boolean; note?: string };
  } | null;
  risk_acknowledged: boolean;
  created_at: string;
};

export type Alert = {
  id: string;
  level: string;
  title: string;
  message: string | null;
  is_read: boolean;
  created_at: string;
};

export type Overview = {
  account: { currency: string };
  paper_account: {
    balance: number;
    equity: number;
    is_active: boolean;
    open_positions: number;
    closed_trades: number;
  };
  active_strategies: number;
  daily_pnl: number;
  drawdown_pct: number;
  risk_alerts: number;
  risk_events: number;
  sessions: { name: string; start: string; end: string; active: boolean }[];
  data_feed: { provider: string; symbols: number; ok: boolean };
  kill_switch: boolean;
  utc_now: string;
  config: { app_env: string; llm_provider: string };
};

export type BacktestJobItem = {
  id: string;
  strategy_id: string;
  strategy_name: string;
  status: string;
  progress: number;
  error: string | null;
  created_at: string;
  metrics: Record<string, number | string | null>;
};

export type BacktestList = {
  total: number;
  items: BacktestJobItem[];
};

export type StrategyCheckItem = {
  check: string;
  severity: "pass" | "info" | "warn" | "fail";
  detail: string;
};

export type IntrabarPreview = {
  "signal-label": string;
  state: string;
  side: string | null;
  rule_ids: string[];
  price: number;
  spread_pips: number;
  ts: number | null;
  is_intrabar: boolean;
  blocked_reason: string | null;
  detail: string | null;
} | null;

export type StrategyCheckReport = {
  strategy_id: string;
  version: string;
  checked_at: string;
  overall: string;
  summary: string;
  checks: StrategyCheckItem[];
  intrabar?: IntrabarPreview;
};

export type ProviderHealthView = {
  status: string;
  latency_ms: number | null;
  detail: string | null;
  checked_at: string;
};

export type ProviderConnectionView = {
  status: string;
  latency_ms: number | null;
  last_connected_at: number | null;
  error: string | null;
};

export type ProviderStatus = {
  active_provider: string;
  active_provider_label: string;
  env_selected: string;
  bid_ask_basis: string;
  health: ProviderHealthView;
  connections: Record<string, ProviderConnectionView>;
  stale_threshold_seconds: number;
};

export type InstrumentView = {
  canonical_symbol: string;
  display_symbol: string;
  provider_symbol: string;
  base_currency: string;
  quote_currency: string;
  pip_size: number;
  price_precision: number;
  contract_size: number;
  minimum_lot: number | null;
  data_provider: string;
  data_delay_status: string;
};

export type QuoteView = {
  symbol: string;
  provider_symbol: string;
  bid: number;
  ask: number;
  mid: number;
  spread_price: number;
  spread_pips: number;
  ts: number;
  timestamp_utc: string;
  latency_ms: number | null;
  source: string;
  market_status: string;
  is_stale: boolean;
  feed_state: string | null;
  provider: string | null;
  bid_ask_basis: string | null;
};

export type FeedHealthRow = {
  symbol: string;
  provider: string;
  feed_status: string;
  last_quote_ts: number | null;
  latency_ms: number | null;
  last_error: string | null;
};

export type CandleView = {
  symbol: string;
  timeframe: string;
  ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  source: string;
  is_complete: boolean;
  bid_ask_basis?: string | null;
};

export type CandleResponse = {
  symbol: string;
  timeframe: string;
  provider: string;
  count: number;
  gaps: number[];
  start: string;
  end: string;
  candles: CandleView[];
};

export type LiveQuoteStreamEvent = {
  type: "quote";
  symbol: string;
  provider: string;
  bid?: number;
  ask?: number;
  mid: number;
  spread_pips?: number;
  latency_ms?: number;
  ts: number;
  market_status?: string;
};

export type LiveCandleStreamEvent = {
  type: "candle_update" | "candle_close";
  symbol: string;
  timeframe: string;
  ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  is_complete: boolean;
};

export type LiveFeedHealthEvent = {
  type: "feed_health";
  symbol: string;
  provider: string;
  feed_status: string;
  latency_ms?: number;
};

export type LiveSignalEvent = {
  type: "signal";
  signal_id: string;
  symbol: string;
  timeframe: string;
  signal: string;
  state: string;
  created_at: string;
};

export type MarketDataStreamEvent =
  | { type: "snapshot"; ts: number; data: { provider_status: ProviderStatus; feed_health: FeedHealthRow[]; ingestion: { running: boolean; provider: string }; utc_now: string } }
  | LiveQuoteStreamEvent
  | LiveCandleStreamEvent
  | LiveFeedHealthEvent
  | LiveSignalEvent;

export type PaperOrderView = {
  id: string;
  position_id: string | null;
  trade_id: string | null;
  strategy_id: string | null;
  symbol: string;
  side: string;
  order_type: string;
  status: string;
  size_units: number;
  stop_loss: number | null;
  take_profit: number | null;
  limit_price: number | null;
  request_ts: number;
  approval_ts: number | null;
  fill_ts: number | null;
  fill_price: number | null;
  rejection_reason: string | null;
  created_at: string;
};

export type PaperFillView = {
  id: string;
  order_id: string;
  position_id: string | null;
  trade_id: string | null;
  ts: number;
  price: number;
  volume: number;
  side: string;
  fill_type: string;
  spread_cost: number;
  slippage_cost: number;
  commission: number;
  bid_ask_basis: string;
  provider: string;
  created_at: string;
};

export type PaperMarginEventView = {
  id: string;
  ts: number;
  event_type: string;
  detail: string | null;
  balance: number;
  equity: number;
  drawdown_pct: number;
  trading_state: string;
  created_at: string;
};