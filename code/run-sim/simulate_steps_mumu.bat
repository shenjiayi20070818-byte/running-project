@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 mumu_sensors.py --duration 30 --cadence 180 --cadence-variation 6 --rate 50 %*
pause
