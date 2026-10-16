"""Data-sanity gate for DexScreener market caps (Oct 2026: TRUMP/CARDS/Veil printed $100M+ 'winners' that were wrong tokens or fake pool prints).
sane_mcap returns 0.0 (= 'no reading') when a DexScreener market cap cannot be a fresh launch, so no entry/outcome is built on it."""
MAX_FRESH_MCAP_USD = 30e6        # nothing launched inside the last hour is genuinely above this
STREAM_MISMATCH_X = 20.0         # DexScreener vs our own PumpPortal stream number


def sane_mcap(mc_usd, stream_mcap_sol, sol_usd):
    """mc_usd: DexScreener marketCap/fdv in USD. stream_mcap_sol: our last PumpPortal marketCapSol for the mint (may be None)."""
    try:
        mc = float(mc_usd or 0)
    except (TypeError, ValueError):
        return 0.0
    if mc <= 0:
        return 0.0
    if mc > MAX_FRESH_MCAP_USD:
        return 0.0
    if stream_mcap_sol and sol_usd and mc > STREAM_MISMATCH_X * float(stream_mcap_sol) * float(sol_usd):
        return 0.0
    return mc
