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