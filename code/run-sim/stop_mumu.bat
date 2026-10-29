@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 mumu.py stop %*
if errorlevel 1 pause
