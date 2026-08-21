@echo off
title HS300-LS 换仓更新
cd /d "%~dp0"

set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%PY%" if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo 按 21 个交易日换仓全自动更新。建议先运行 HS300-LS-install-auto.cmd。
echo 本窗口是常驻检查；已装计划任务则可关掉。
"%PY%" "%~dp0run_update.py" --install
"%PY%" "%~dp0run_update.py"
if errorlevel 1 pause
