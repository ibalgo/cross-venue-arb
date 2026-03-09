# Cross-Venue Arbitrage Engine (Kalshi ↔ Polymarket)

### What it is:
* Real-time scanner for equivalent contracts across venues
* Normalizes fees + depth to compute true "net edge"
* Executes arb opportunities automatically

### Key pieces:
* Pair map (Kalshi ticker ↔ PM market/outcome) + confidence (similarity score)
* Effective price model (VWAP across top levels + fees)
* Filters: min edge, min depth, max time-to-resolution

### Execution:
* Hedged two-leg execution with timeouts (IOC/FOK where possible)
* Handles partial fills + reconciliation
* Auto-unwind policy + kill switch (stale data, rejects, position drift)

### Deliverables:
* Live dashboard: top arbs ranked by net edge + available size
* One-click / auto mode execution + trade timeline
* Post-trade report: locked edge vs realized PnL + fees + slippage
