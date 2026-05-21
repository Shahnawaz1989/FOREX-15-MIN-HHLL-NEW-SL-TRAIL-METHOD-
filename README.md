# Forex-15M-HHLL

15-minute forex trading bot / backtest project with live signal generation, MT5 integration, HH/LL logic, and registry-based order state handling.

## Main purpose
This repo is used as the single source of truth for code + context, so a new discussion can start from one repo link without re-pasting files.

## Main areas
- Live signal generation
- HH/LL setup logic
- Registry-based order state tracking
- MT5 order bridge / signal files
- Backtest utilities
- Session and gate timing logic

## Important docs
- `docs/PROJECT_CONTEXT.md`
- `docs/BUG_LOG.md`
- `docs/ARCHITECTURE_MAP.md`
- `docs/HANDOFF_PROMPT.md`

## Current workflow
1. Code changes happen locally in VS Code
2. Changes are committed and pushed to this repo
3. New thread starts with this repo link
4. Repo + docs are read first
5. Then ready-to-paste fixes are given

## Notes
- Do not rely on runtime/temp files as source of truth
- Keep secrets and credentials out of the public repo
- Prefer modular refactor without breaking live behavior