# PROJECT_CONTEXT

## Project
Forex 15M HH/LL trading bot with live and backtest components.

## Main goal
Maintain a codebase that supports:
- live signal generation
- backtest validation
- registry-based trade state tracking
- MT5 file bridge execution

## Current focus
- Prevent duplicate/repeated orders after TP or SL hit
- Improve registry and reconcile flow
- Keep live behavior stable while refactoring
- Move toward modular engine structure

## Current architecture
Main flow currently mixes:
- setup generation
- HH/LL logic
- registry update/read logic
- reconcile logic
- signal file export
- cancel/replace logic

## Target architecture
- `main_engine.py` -> orchestration only
- `hhll_engine.py` -> setup generation only
- `reconcile_engine.py` -> entry-hit / TP / SL / expiry scan
- `order_state_engine.py` -> active/completed/cancel rules
- `registry_manager.py` -> registry persistence only
- `signal_file_manager.py` -> BUY/SELL signal file handling only

## Trading rules/preferences to preserve
- Raw server time preferred
- Live behavior should not break due to refactor
- Registry state should remain reliable
- Changes should be low-risk and easy to paste/apply

## Current branch
main

## Last stable tag
Not set yet

## Last working summary
Initial public repo created and first clean project upload pushed.