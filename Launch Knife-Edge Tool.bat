@echo off
cd /d "%~dp0"
python knife_edge_app.py
if errorlevel 1 pause

