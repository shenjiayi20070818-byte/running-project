@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 mumu_location.py --scenario moving --duration 30 %*
pause
