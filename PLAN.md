# Polymarket–Kalshi Arbitrage Engine — Plan

## Overview

```
Phase 1: Schema & DB         → data models, tables, config
Phase 2: Ingestion (REST)    → market metadata + orderbook snapshots
Phase 3: WebSocket Streaming → live orderbook updates + reconnect logic
Phase 4: Market Matching     → pair Kalshi ↔ PM markets with confidence scores
Phase 5: Arb Detection       → VWAP pricing, net edge, opportunity ranking
Phase 6: Execution           → two-leg hedged orders, partial fills, kill switch
Phase 7: Dashboard           → live arb feed, one-click execution, post-trade PnL
```

---

## Phase 1 — Schema & DB

**1.1 DB Models**

| Model | Key fields |
|---|---|
| `Market` | `id`, `source`, `source_id`, `title`, `outcomes`, `resolution_criteria`, `close_time`, `status`, `fee_maker`, `fee_taker` |
| `OrderbookSnapshot` | `market_id`, `outcome_id`, `bids`, `asks`, `last_price`, `volume_24h`, `sequence_number`, `snapshot_at` |
| `MarketPair` | `pair_id`, `kalshi_market_id`, `pm_market_id`, `confidence_score`, `match_method`, `status` |
| `ArbOpportunity` | `pair_id`, `kalshi_side`, `pm_side`, `kalshi_eff_price`, `pm_eff_price`, `net_edge`, `available_size`, `status` |
| `Execution` | `opportunity_id`, `leg1_fill`, `leg2_fill`, `locked_edge`, `realized_pnl`, `fees`, `slippage` |

**1.2 Config**
- Load `.env` with `python-dotenv`; expose `KALSHI_API_KEY`, `PM_API_KEY`, `PM_PRIVATE_KEY`
- Raise at startup if required keys are missing
- SQLite for dev, Postgres for prod (one-line config change)

**Deliverable:** `python main.py` starts with empty DB, all tables created, no errors.

---

## Phase 2 — Ingestion (REST)

**Tasks**
- Kalshi client: paginate `GET /markets` + `GET /markets/{ticker}/orderbook`, map to unified schema
- Polymarket client: paginate `GET /markets` + `GET /book`, map to unified schema
- Raw events table: append-only JSON store with `idempotency_key` (hash of `source_id + timestamp`)
- Scheduler: market refresh every 5 min; orderbook snapshots every 10–30s for active pairs

**Deliverables**
- [ ] `ingestion/kalshi_rest.py`, `ingestion/polymarket_rest.py`
- [ ] `db/models.py` — all tables
- [ ] `scheduler.py` — APScheduler job runner

---

## Phase 3 — WebSocket Streaming

**Tasks**
- Polymarket: subscribe to `book` + `trade` channels; apply delta updates by `sequence`
- Kalshi: subscribe to `orderbook_delta` + `trade`; handle `yes`/`no` side model
- On sequence gap: discard local state → re-fetch REST snapshot → re-sync
- Reconnect: exponential backoff (1s → 60s max) + heartbeat watchdog (15s timeout)
- Internal event bus (`asyncio` queues): topics `orderbook.update`, `trade.new`, `market.status_change`

**Deliverables**
- [ ] `streaming/polymarket_ws.py`, `streaming/kalshi_ws.py`
- [ ] `streaming/event_bus.py`
- [ ] Integration tests: gap injection, reconnect, sequence replay

---

## Phase 4 — Market Matching

**Tasks**
- Similarity pipeline: title token overlap + edit distance, outcome label alignment, resolution date window, category prior → weighted confidence score
- Auto-accept threshold: ≥ 0.85; below that, queue for manual review
- CLI review tool: show pairs side-by-side; accept / reject / override; stored in `match_overrides`
- Nightly re-match job: catch new markets, retire closed pairs, flag re-review on rule changes

**Deliverables**
- [ ] `matching/scorer.py`, `matching/cli.py`, `matching/maintenance.py`
- [ ] `market_pairs`, `match_overrides` tables
- [ ] Eval script: precision/recall on labeled sample (target ≥ 90% at auto-accept threshold)

---

## Phase 5 — Arb Detection

**Tasks**
- Effective price: VWAP across top N levels up to target notional ± `fee_taker`
- `net_edge = abs(eff_pm_price - eff_kalshi_price)` — both directions evaluated
- Filters: `min_net_edge` (e.g. 1.5¢), `min_available_size`, `max_time_to_resolution`, `max_snapshot_age`
- Rank by `net_edge × available_size`; suppress re-detection while execution in flight

**Deliverables**
- [ ] `arb/pricer.py` — VWAP + fee model
- [ ] `arb/detector.py` — filter + ranking pipeline
- [ ] Backtest harness: replay snapshots, measure opportunity frequency + size distribution

---

## Phase 6 — Execution

**Tasks**
- Order routing: Kalshi `POST /portfolio/orders`, Polymarket `POST /order` (signed); IOC/FOK where available
- Leg sequencing: less-liquid leg first; leg 2 sized to leg 1 fill
- Partial fills: compute residual → attempt auto-unwind within N seconds → alert + halt if still open
- Kill switch: global + per-pair; persisted to DB (survives restart); triggers: stale data, consecutive rejects, drawdown limit
- Reconciliation: `locked_edge` vs `realized_pnl` vs fees vs slippage per execution

**Deliverables**
- [ ] `execution/router.py`, `execution/hedger.py`, `execution/risk.py`, `execution/reconciler.py`
- [ ] E2E integration tests with mocked order APIs

---

## Phase 7 — Dashboard & Reporting

**Tasks**
- Live feed: top arbs ranked by net edge × size, WS stream health, open positions
- Controls: one-click execution (manual mode), auto mode toggle, per-pair size limits
- Post-trade report: locked edge vs realized PnL vs fees vs slippage; daily summary export
- Alerts: data lag, leg failures, auto-unwind triggered, large edge detected

**Deliverables**
- [ ] `dashboard/` — FastAPI backend + lightweight frontend
- [ ] `reporting/pnl.py`
- [ ] `alerts/` — Slack/email/webhook dispatcher

---

## Dependency Graph

```
Phase 1 (Schema & DB)
    └── Phase 2 (Ingestion)
            ├── Phase 3 (Streaming)  ─┐
            └── Phase 4 (Matching)    ├── Phase 5 (Detection) → Phase 6 (Execution) → Phase 7 (Dashboard)
```

Phases 3 and 4 both depend on Phase 2 but are independent of each other.

---

## Work Division (2 developers)

### Developer A — Data & Execution
Owns all deterministic components: ingestion, streaming, matching, detection, order routing.

| Phase | Work |
|---|---|
| Phase 1 | DB models, config, project structure |
| Phase 2 | Kalshi + Polymarket REST clients, scheduler |
| Phase 3 | WS connection managers, delta application, reconnect logic |
| Phase 4 | Similarity scorer, nightly maintenance job |
| Phase 5 | VWAP pricer, detector, backtest harness |
| Phase 6 | Order router, hedger, reconciler |

### Developer B — Matching intelligence, Risk & Dashboard
Owns manual review tooling, risk controls, and the user-facing layer.

| Phase | Work |
|---|---|
| Phase 1 | Pydantic schemas, `requirements.txt` |
| Phase 4 | Manual review CLI, `match_overrides`, eval script |
| Phase 5 | Filter tuning, opportunity ranking |
| Phase 6 | Kill switch + risk module, E2E integration tests |
| Phase 7 | Dashboard UI, post-trade reporting, alerting |

---

## Stack

| Component | Choice |
|---|---|
| Language | Python 3.11+ |
| Async | `asyncio` + `aiohttp` |
| DB | PostgreSQL (prod) / SQLite (dev) |
| ORM | SQLAlchemy 2.x |
| Validation | Pydantic v2 |
| Scheduler | APScheduler |
| API | FastAPI |
| Tests | pytest + pytest-asyncio |
| Infra | Docker + docker-compose |
