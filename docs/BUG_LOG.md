# BUG_LOG

## Active bugs

### Bug 1
Title: Repeated order after TP/SL hit

Symptoms:
A new order may be generated again even after TP or SL already completed for a previous signal.

Expected behavior:
Once completed trade is confirmed in registry, same setup or blocked scope should not generate again.

Actual behavior:
Fresh signal may still be created depending on registry/reconcile timing.

Suspected modules:
- reconcile logic
- registry update logic
- fresh signal write flow
- payload build flow

Status:
Open

---

### Bug 2
Title: Registry and live signal file state mismatch

Symptoms:
Registry may show one state while signal files still allow fresh signal write or cancel/replace flow.

Expected behavior:
Registry and signal file flow should remain aligned.

Actual behavior:
Stale file / stale registry state can affect next cycle behavior.

Suspected modules:
- registry manager
- signal file manager
- write fresh signal flow

Status:
Open

---

### Bug 3
Title: Engine overload due to mixed responsibilities

Symptoms:
Too much logic in one engine makes debugging and safe edits harder.

Expected behavior:
Logic should be split into focused modules.

Actual behavior:
HH/LL, reconcile, registry, and signal export are tightly mixed.

Suspected modules:
- main engine

Status:
Open

## Recent findings
- Pair/day completed lock exists but depends on reliable registry update
- Reconcile timing is important for stopping repeated signals
- Strict file write and registry write need better separation

## Verified fixed
- Initial GitHub repo created
- Clean push workflow established

## Do not break
- Live execution behavior
- Registry integrity
- Existing stable signal file format
- MT5 bridge compatibility