# ARCHITECTURE_MAP

## Current high-level flow
1. Load latest market data
2. Reconcile open registry signals with market data
3. Build HH/LL setups
4. Apply order-state rules
5. Export live BUY/SELL signal files
6. Update registry

## Current major areas
- Data fetch / preparation
- Setup generation
- Registry management
- Reconcile / outcome scan
- Live signal export
- Cancel / replace handling
- Backtest support

## Target module responsibilities

### main_engine.py
- Call other modules
- Keep orchestration only
- Avoid deep business logic

### hhll_engine.py
- HH/LL setup generation
- disable-window logic
- setup selection logic

### reconcile_engine.py
- scan candles for entry hit
- scan TP / SL / expiry
- same-candle resolution
- return normalized outcome

### order_state_engine.py
- completed / active / cancel rules
- pair/day and side-level block rules
- decision guard before new signal creation

### registry_manager.py
- create registry file
- load/save registry
- mark completed/non-completed/entry-hit
- query completed and active rows

### signal_file_manager.py
- read current BUY/SELL files
- write PLACE/CANCEL payloads
- compare payloads
- maintain file format compatibility

## Important design rule
Main engine should call modules; modules should not tightly mutate unrelated parts unless necessary.

## Refactor direction
Refactor should be safe and gradual, not a full risky rewrite in one step.