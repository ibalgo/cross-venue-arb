# cross-venue-arb

A cross-venue arbitrage bot that detects and executes profitable price discrepancies across decentralized exchanges (DEXs).

## Overview

This project monitors token prices across multiple DEX venues in real time and identifies arbitrage opportunities where the same asset trades at different prices. When a profitable spread is found (after accounting for gas costs and fees), the bot can execute trades to capture the difference.

## Features

- Real-time price monitoring across multiple DEX venues (Uniswap, SushiSwap, Curve, etc.)
- Configurable token pair and venue selection
- Gas-aware profit estimation — filters out unprofitable opportunities
- Modular architecture: easily add new venues or strategies
- Logging and alerting for detected opportunities and executed trades

## Architecture

```
cross-venue-arb/
├── src/
│   ├── monitors/       # Price feed listeners per venue
│   ├── strategies/     # Arbitrage detection logic
│   ├── executors/      # Trade execution handlers
│   └── utils/          # Shared helpers (gas estimation, token math, etc.)
├── config/
│   └── settings.yaml   # Venue endpoints, token pairs, thresholds
├── tests/
└── README.md
```

## Getting Started

### Prerequisites

- Python 3.10+ (or Node.js 18+ depending on implementation)
- Access to an RPC endpoint (e.g., Alchemy, Infura, or a local node)
- A funded wallet private key for execution (optional for monitoring-only mode)

### Installation

```bash
git clone https://github.com/ibalgo/cross-venue-arb.git
cd cross-venue-arb
pip install -r requirements.txt
```

### Configuration

Copy the example config and fill in your values:

```bash
cp config/settings.example.yaml config/settings.yaml
```

Key fields:

| Field | Description |
|---|---|
| `rpc_url` | Your Ethereum RPC endpoint |
| `private_key` | Wallet key for executing trades |
| `token_pairs` | List of token pairs to monitor |
| `venues` | List of DEX venues to compare |
| `min_profit_usd` | Minimum net profit threshold to act on |

### Usage

**Monitor only (no execution):**

```bash
python main.py --monitor
```

**Live trading mode:**

```bash
python main.py --execute
```

## How It Works

1. **Price polling** — Fetch spot prices for a token pair from each configured venue.
2. **Spread calculation** — Compute the spread between the cheapest buy and most expensive sell venue.
3. **Profit check** — Subtract estimated gas costs and fees; skip if net profit is below threshold.
4. **Execution** — Route a buy on the low-price venue and a sell on the high-price venue atomically (or in sequence depending on strategy).

## Supported Venues

| Venue | Protocol |
|---|---|
| Uniswap V2/V3 | AMM |
| SushiSwap | AMM |
| Curve Finance | Stableswap AMM |
| Balancer | Weighted AMM |

Additional venues can be added by implementing the `BaseMonitor` interface in `src/monitors/`.

## Risks & Disclaimers

- **Front-running / MEV** — On-chain arbitrage is competitive. Transactions may be sandwiched or front-run.
- **Smart contract risk** — Interacting with third-party protocols carries inherent risk.
- **Slippage** — Large trades relative to pool liquidity may erode expected profits.
- This project is for **educational purposes**. Use at your own risk.

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

## License

MIT
# cross-venue-arb
