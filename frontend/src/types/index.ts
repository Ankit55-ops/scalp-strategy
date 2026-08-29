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
  risk_per_trade_pct: number;
  max_daily_loss_pct: number;
  max_consecutive_losses: number;
  max_open_positions: number;
  max_trades_per_day: number;
  max_correlated_exposure_pct: number;
  max_spread_pips: number;
  hard_stop_distance_pips: number;
  news_blackout_minutes_before: number;
  news_blackout_minutes_after: number;
};

export type PaperStatus = {
  is_active: boolean;
  balance: number;
  equity: number;
  open_positions: number;
  closed_trades: number;
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