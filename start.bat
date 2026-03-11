@echo off
set PORT=8080
if not "%1"=="" set PORT=%1
echo Starting QuestLog on port %PORT%...
python server.py --port %PORT%
pause
