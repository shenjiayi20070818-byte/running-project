@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 mumu_waypoint.py --point 0 --hold 20 %*
pause
