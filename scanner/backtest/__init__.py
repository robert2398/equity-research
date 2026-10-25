"""Walk-forward backtest of scanner signals.

For EVERY completed trading session t (cutoff), all data is truncated to <= t
and the same detection stack as the live scan is executed. Every primary setup
appearing that day is recorded (first appearance only, deduplicated), then
forward moves are measured over 5/10/21/42/63 sessions (1W/15D/1M/2M/3M):

  - close(t) -> close(t+N)              signal-day close basis
  - open(t+1) -> close(t+N)             next-open entry (T+1 realistic)
  - excess vs Nifty 500 (same horizons)

evaluate.py then scores the events: actionable filter (NEW/RECENT, no backlog),
excess vs an equal-weight universe baseline on the same dates, payoff stats,
month-clustered Newey-West t-stats and a capital-constrained portfolio sim.

Honest limitations (also written into the report): current-constituent universe
(survivorship bias), no dividend adjustment, flat costs in the sim only, thresholds NOT
tuned on these results, descriptive statistics — not a profitability claim.
"""
from .walkforward import run_backtest, score_saved_events, summarize_events, write_backtest_report

__all__ = ["run_backtest", "score_saved_events", "summarize_events", "write_backtest_report"]
