# Changes made 11 Nov 2026

## 1. Compound+Sprint cascade mechanism (replaces old split mechanisms)
- **Commit:** `8fd221e`
- **File changed:** `executor/books.py`
- **What it does:** new `check_sprint_compound_cascade()` — fires ONCE, the first time either compound's or sprint's own `banked()` reaches $200; pulls that $200 from whichever module earned it; credits core1+core2 $100 each. Retires (not deletes) the two older cascade mechanisms (`check_compound_split()`, `_maybe_fire_phase1_cascade()`) as permanent no-ops.
- **Live-only gate:** only fires if `LIVE_BOOKS` env includes the funding module — permanently inert on the current paper run. Confirmed inert both before and after today's incident.
- **Sandbox-tested** before touching live code (fire-once, single-funder deduction, live-gate behavior all verified).

## 2. phase_target.py real 5-phase fix
- **Commit:** `94c4bd8`
- **File changed:** `executor/phase_target.py`
- Replaced stale A/B/C/D structure with the real confirmed 5-phase values matching `PHASE_DEFS` in `books.py`.

## 3. Dual-module (compound+sprint) 5-phase projection, $200 funding start
- Built using each module's exact real banking formula (not a proxy — an earlier attempt had a proxy bug, caught and disclosed before presenting numbers). Two pace scenarios (optimistic/conservative). Full tables in chart artifact + Notion + `PHASE_TARGETS_SUMMARY.md`.

## 4. 🚨 Trade history reset incident — found, recovered, guarded
- **~16:34–16:44 PKT today:** `paper_closed_positions` dropped from 129 to 3 closed trades; `books_deposits_v1` (old cascade's $66.66/$66.67/$66.67 deposits) and `compound_split_v1` also wiped. Second occurrence of this exact failure shape (first was 9 Nov 2026).
- **Root cause:** not pinned to one exact line of code — every write path in this repo only appends to trade history; no automatic reset function exists. Leading theory: something outside the Python process (Windows-side file interference — sync tool, backup agent, or AV), consistent with repeated `PermissionError: [WinError 5] Access is denied` traces in the same window. The new cascade mechanism and the phase_target fix (both committed earlier today) are ruled out — neither has ever fired, on either side of the reset.
- **Recovery:** merged the pre-reset snapshot (129 trades) with the 23 genuine trades that closed live since the reset — zero overlap, clean union to 152 closed trades. Restored the wiped deposits/split record. Done via `state.py`'s own `atomic_multi_fn()`, verified stable across two spaced checks under live write load.
- **Fix, commit `6f31b03`:** added a shrink guard in `state.py::_local_save_all` (the single chokepoint every local write passes through) — refuses any save that would collapse `paper_closed_positions` or `books_deposits_v1` by 20+ entries down to below 50% of the current count; live file left untouched; dangerous payload saved to a side file instead of applied. Normal trade-close appends are unaffected.
- **Testing:** dedicated sandbox tests (append passes, drastic shrink blocked via both direct save and `atomic_multi_fn`, small legitimate decrease still allowed) + full `tests/test_state*.py` + `tests/test_paper_ledger.py` — 84/84 passing.
- **Scope discipline:** no trading/entry/exit/scoring logic touched by any of the above.
