@echo off
rem Startet die App direkt aus dem Quellcode (zum Testen, ohne Installation)
cd /d "%~dp0"
python -m pip install -q -r requirements.txt
python -m jc.launcher
