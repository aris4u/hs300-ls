@echo off
title HS300-LS 安装全自动更新
cd /d "%~dp0"

set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%PY%" if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo 写入 Windows 计划任务：每个交易日 15:25 后自动检查换仓，登录后再查一次。
echo 关界面也会跑。电脑关机时不会跑，开机登录后会补。
"%PY%" "%~dp0run_update.py" --install
if errorlevel 1 (
  echo.
  echo 安装失败。
  pause
  exit /b 1
)
echo.
echo 已装好。可在「任务计划程序」里看到 HS300-LS-rebalance。
pause
