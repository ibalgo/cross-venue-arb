""" Maintains the Polymarket WebSocket connection; applies orderbook deltas and handles reconnect/gap recovery. """

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

log = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

CLOB_WS_URL    = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CLOB_REST_URL  = "https://clob.polymarket.com/book"
GAMMA_API_URL  = "https://gamma-api.polymarket.com/markets"

PING_INTERVAL   = 10   # seconds between keepalive pings
RECONNECT_DELAY = 5    # seconds before reconnecting after a drop


# ─── OrderBook ────────────────────────────────────────────────────────────────

class OrderBook:
    """
    In-memory orderbook for a single token (Yes or No).

    Stored as dicts { price -> size } for O(1) updates.
    size == 0 on a delta means remove that price level.
    """

    def __init__(self, asset_id: str):
        self.asset_id = asset_id
        self.bids: dict[float, float] = {}   # price -> size
        self.asks: dict[float, float] = {}
        self.last_updated: float = 0.0
        self.synced: bool = False            # True after first snapshot received

    # ── Mutations ─────────────────────────────────────────────────────────────

    def apply_snapshot(self, bids: list, asks: list) -> None:
        """Wipe and replace the entire book. Called on connect + gap recovery."""
        for obj in bids:
            p, s = float(obj["price"]), float(obj["size"])
            self.bids[p] = s
        
        for obj in asks:
            p, s = float(obj["price"]), float(obj["size"])
            self.asks[p] = s

        self.last_updated = time.time()
        self.synced = True
        log.debug(f"[{self.asset_id[:10]}] snapshot: {len(self.bids)} bids / {len(self.asks)} asks")

    def apply_delta(self, side: str, price: float, size: float) -> None:
        """Update a single price level. size=0 removes the level."""
        book = self.bids if side == "bids" else self.asks
        if size == 0.0:
            book.pop(price, None)
        else:
            book[price] = size
        self.last_updated = time.time()

    # ── Derived values ────────────────────────────────────────────────────────

    @property
    def best_bid(self) -> Optional[float]:
        return max(self.bids) if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return min(self.asks) if self.asks else None

    @property
    def mid(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return round((self.best_bid + self.best_ask) / 2, 4)
        return None

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return round(self.best_ask - self.best_bid, 4)
        return None

    def sorted_bids(self) -> list[tuple[float, float]]:
        """All bids, highest first."""
        return sorted(self.bids.items(), reverse=True)

    def sorted_asks(self) -> list[tuple[float, float]]:
        """All asks, lowest first."""
        return sorted(self.asks.items())

    def vwap(self, side: str, depth: float) -> Optional[float]:
        """
        Volume-weighted average price for buying/selling `depth` shares.

        Returns None if there isn't enough liquidity.
        """
        levels = self.sorted_asks() if side == "buy" else self.sorted_bids()
        remaining = depth
        total_cost = 0.0
        for price, size in levels:
            fill = min(size, remaining)
            total_cost += fill * price
            remaining  -= fill
            if remaining <= 0:
                return round(total_cost / depth, 4)
        return None

    def __repr__(self) -> str:
        return (
            f"OrderBook({self.asset_id[:10]}... "
            f"bid={self.best_bid} ask={self.best_ask} "
            f"mid={self.mid} spread={self.spread})"
        )


# ─── MarketState ──────────────────────────────────────────────────────────────

@dataclass
class MarketState:
    """
    All real-time data for a Polymarket market.
    """
    condition_id:      str
    question:          str          # human-readable question
    yes_token:         str          # token ID for Yes
    no_token:          str          # token ID for No
    end_date:          str          # resolution date

    yes_book:          OrderBook
    no_book:           OrderBook

    last_trade_price:  Optional[float] = None
    last_trade_time:   Optional[float] = None

    # ── Convenience accessors (what arb engine will read) ─────────────────────

    @property
    def yes_mid(self) -> Optional[float]:
        return self.yes_book.mid

    @property
    def no_mid(self) -> Optional[float]:
        return self.no_book.mid

    @property
    def yes_best_bid(self) -> Optional[float]:
        return self.yes_book.best_bid

    @property
    def yes_best_ask(self) -> Optional[float]:
        return self.yes_book.best_ask
    
    @property
    def no_best_bid(self) -> Optional[float]:
        return self.no_book.best_bid

    @property
    def no_best_ask(self) -> Optional[float]:
        return self.no_book.best_ask

    def __repr__(self) -> str:
        return (
            f"MarketState({self.condition_id[:10]}... "
            f"YES mid={self.yes_mid} NO mid={self.no_mid})"
        )

# ─── WebSocket client ─────────────────────────────────────────────────────────

class PolymarketWSClient:
    """
    Maintains live orderbook state for a set of Polymarket markets.

    Flow:
      1. fetch_active_markets() → get condition IDs + token IDs
      2. On connect: fetch REST snapshots for all tokens (gap recovery baseline)
      3. Subscribe to WS market channel
      4. Apply book snapshots + price_change deltas in real time
      5. On disconnect: mark books unsynced, reconnect, re-snapshot

    Read state from anywhere via:
      state = client.get_state(condition_id)
      state.yes_mid, state.yes_book.sorted_bids(), etc.
    """

    def __init__(
        self,
        markets: list[dict],
        on_update: Optional[Callable[[MarketState], None]] = None,
    ):
        self.on_update = on_update or self._default_on_update
        self._running  = False

        self._states:    dict[str, MarketState]          = {}
        self._token_map: dict[str, tuple[str, str]]      = {}  # token_id -> (cid, "yes"|"no")

        for m in markets:
            cid = m["condition_id"]
            yes = m["yes_token"]
            no  = m["no_token"]

            state = MarketState(
                condition_id = cid,
                question     = m.get("question", ""),
                yes_token    = yes,
                no_token     = no,
                end_date     = m.get("end_date", ""),
                yes_book     = OrderBook(yes),
                no_book      = OrderBook(no),
            )
            self._states[cid]  = state
            self._token_map[yes] = (cid, "yes")
            self._token_map[no]  = (cid, "no")

        log.info(f"PolymarketWSClient initialized with {len(self._states)} markets")

    # ── Public accessors ──────────────────────────────────────────────────────

    def get_state(self, condition_id: str) -> Optional[MarketState]:
        return self._states.get(condition_id)

    def all_states(self) -> list[MarketState]:
        return list(self._states.values())

    @property
    def _all_token_ids(self) -> list[str]:
        return list(self._token_map.keys())

    # ── Gap recovery ──────────────────────────────────────────────────────────

    async def _fetch_snapshot(self, token_id: str) -> None:
        """Fetch one token's orderbook from REST and apply it."""
        url = f"{CLOB_REST_URL}?token_id={token_id}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
            cid, side = self._token_map[token_id]
            book = self._get_book(cid, side)
            book.apply_snapshot(
                bids=data.get("bids", []),
                asks=data.get("asks", []),
            )
        except Exception as e:
            log.error(f"Snapshot failed for {token_id[:10]}: {e}")

    async def _fetch_all_snapshots(self) -> None:
        """Fetch all token snapshots in parallel."""
        log.info(f"Fetching REST snapshots for {len(self._all_token_ids)} tokens...")
        await asyncio.gather(*[
            self._fetch_snapshot(tid) for tid in self._all_token_ids
        ])

    # ── Message handling ──────────────────────────────────────────────────────

    def _handle_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        events = data if isinstance(data, list) else [data]
        for event in events:
            etype = event.get("event_type") or event.get("type")
            if etype == "book":
                self._on_book(event)
            elif etype == "price_change":
                self._on_price_change(event)
            elif etype == "last_trade_price":
                self._on_last_trade(event)

    def _on_book(self, event: dict) -> None:
        """Full WS snapshot — replace entire book."""
        token_id = event.get("asset_id", "")
        if token_id not in self._token_map:
            return
        cid, side = self._token_map[token_id]
        self._get_book(cid, side).apply_snapshot(
            bids=event.get("bids", []),
            asks=event.get("asks", []),
        )
        self.on_update(self._states[cid])

    def _on_price_change(self, event: dict) -> None:
        """Delta update — apply only changed price levels."""
        changes = event.get("price_changes") or [event]
        for change in changes:
            token_id = change.get("asset_id") or event.get("asset_id", "")
            if token_id not in self._token_map:
                continue
            cid, side = self._token_map[token_id]
            book = self._get_book(cid, side)

            if not book.synced:
                # Got a delta before snapshot — skip, snapshot will come on reconnect
                log.warning(f"Delta before snapshot for {token_id[:10]}, skipping")
                continue
        
            price, size = float(change["price"]), float(change["size"])
            book.apply_delta("bids" if change["side"] == "SELL" else "asks", price, size)

            self.on_update(self._states[cid])

    def _on_last_trade(self, event: dict) -> None:
        token_id = event.get("asset_id", "")
        if token_id not in self._token_map:
            return
        cid, _ = self._token_map[token_id]
        self._states[cid].last_trade_price = float(event.get("price", 0))
        self._states[cid].last_trade_time  = time.time()

    def _get_book(self, cid: str, side: str) -> OrderBook:
        return self._states[cid].yes_book if side == "yes" else self._states[cid].no_book

    # ── Connection loop ───────────────────────────────────────────────────────

    async def run(self) -> None:
        """Stream forever, reconnecting on drops."""
        self._running = True
        while self._running:
            try:
                await self._fetch_all_snapshots()
                await self._connect_and_stream()
            except Exception as e:
                log.error(f"Connection error: {e}. Retrying in {RECONNECT_DELAY}s...")
                self._mark_unsynced()
                await asyncio.sleep(RECONNECT_DELAY)

    async def stop(self) -> None:
        self._running = False

    def _mark_unsynced(self) -> None:
        """After a disconnect, reject deltas until next snapshot."""
        for state in self._states.values():
            state.yes_book.synced = False
            state.no_book.synced  = False

    async def _connect_and_stream(self) -> None:
        log.info(f"Connecting to {CLOB_WS_URL}...")
        async with websockets.connect(CLOB_WS_URL, ping_interval=None) as ws:
            log.info(f"Connected — subscribing to {len(self._all_token_ids)} tokens")
            await ws.send(json.dumps({
                "assets_ids": self._all_token_ids,
                "type": "market",
            }))
            ping_task = asyncio.create_task(self._ping_loop(ws))
            try:
                async for raw in ws:
                    self._handle_message(raw)
            except ConnectionClosed as e:
                log.warning(f"WS closed: {e}")
            finally:
                ping_task.cancel()

    async def _ping_loop(self, ws) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL)
            try:
                await ws.send(json.dumps({"type": "PING"}))
            except Exception:
                break

    # ── Default callback ──────────────────────────────────────────────────────

    def _default_on_update(self, state: MarketState) -> None:
        log.info(
            f"[{state.condition_id[:8]}] {state.question[:40]:<40} | "
            f"YES bid={state.yes_best_bid} ask={state.yes_best_ask} mid={state.yes_mid}"
        )

async def main():
    markets = [ {
    "condition_id": "0x32b09f6390252b37d674501527e709016d55581b2c1e544bd4b8167f5f732f4c",
    "question": "Will Jesus Christ return before GTA VI?",
    "yes_token": "90435811253665578014957380826505992530054077692143838383981805324273750424057",
    "no_token": "92388629082681805622801622703528982922543286352927708208755887536971583436902",
    "end_date": "2026-07-31T12:00:00Z"
  }]

    if not markets:
        log.error("Error in fetching markets")
        return

    print(f"\nTracking {len(markets)} markets:")
    for m in markets[:5]:
        print(f"  {m['condition_id'][:10]}... | {m['question'][:60]}")
    if len(markets) > 5:
        print(f"  ... and {len(markets) - 5} more\n")

    client = PolymarketWSClient(markets=markets)
    await client.run()


if __name__ == "__main__":
    asyncio.run(main())