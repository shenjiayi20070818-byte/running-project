@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 step_sim.py --duration 1800 --cadence 180 --cadence-variation 6 %*
if errorlevel 1 pause
