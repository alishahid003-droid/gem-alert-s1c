@echo off
REM One click: pulls latest code, runs both backtests, dumps system health into logs\check_*.txt.
REM Claude reads those files from this folder -- you only double-click / run this and say "done".
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
echo === pull ===
git pull
echo === backtest 1 (top pump.fun graduates, ~10 min) ===
python backtest_sniper.py --source top --pages 5 > logs\check_backtest_top.txt 2>&1
echo === backtest 2 (newest graduates) ===
python backtest_sniper.py --source new --pages 10 > logs\check_backtest_new.txt 2>&1
echo === health ===
(echo --- diag stream ---) > logs\check_health.txt 2>&1
python -u diag_early_stream.py >> logs\check_health.txt 2>&1
python -c "import multichain_sniper as m;from layers.layer0_scoring import fetch_geckoterminal_new_pools as f;[print(s, 'OK' if (f(s).get('ok')) else 'FAIL') for s in ['solana','bsc','base','robinhood-chain','robinhood']]" >> logs\check_health.txt 2>&1
python -c "from layers.layer0c_stonkfun_scoring import fetch_new_stonkfun_tokens as f;r=f(5);print('stonkfun', r['ok'])" >> logs\check_health.txt 2>&1
echo === ALL DONE - tell Claude "checks done" ===
pause
