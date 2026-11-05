"""Standalone entry point for the aggressive paper-testing lane (Ali, 6 Nov
2026). Run this as its OWN separate process, in its own terminal window,
alongside -- never instead of -- your existing `python local_runner.py`.

    python run_aggressive_lane.py

This does not start, stop, pause, or touch local_runner.py, early-watch,
the multichain sniper, radar, or any of the 4 real books / solana_* books
in any way. It is a completely separate Python process with its own
in-memory state, writing only to the new "aggressive_lane_v1" state key.
Closing this window stops ONLY this lane -- your real system (and its
real/paper books) keeps running untouched in its own window.

Check progress anytime, from a THIRD window, without stopping this one:

    python -c "from executor.aggressive_lane import get_status; print(get_status())"

Tune the lane via environment variables before starting it (all optional,
all isolated to this lane -- none of these names are read by any other
part of the system):
    AGGR_SEED_USD            (default 100.0)
    AGGR_EXPOSURE_MULTIPLIER (default 3.0 -- Ali asked for "2x or 3x")
    AGGR_BASE_STAKE_PCT      (default 0.12 -- 12% of lane equity per trade, before the multiplier)
    AGGR_MAX_CONCURRENT      (default 4)
    AGGR_TARGET_USD          (default 20000.0)
    AGGR_WINDOW_HOURS        (default 48.0 -- Ali: "stretch this 24 hours target to 48 hours")
    AGGR_STOP_LOSS_PCT       (default 0.35)
    AGGR_TIME_STOP_SECONDS   (default 90, in MINUTES -- i.e. 90 minutes)
    AGGR_CYCLE_SECONDS       (default 15)
"""
from executor.aggressive_lane import main

if __name__ == "__main__":
    main()
