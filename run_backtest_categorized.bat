@echo off
cd /d "%~dp0"
echo Running categorized backtest at %date% %time% > backtest_last_run.log
python backtest_categorized.py >> backtest_last_run.log 2>&1
echo Done at %date% %time% >> backtest_last_run.log
