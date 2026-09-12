@echo off
setlocal
cd /d "%~dp0"

set "FR5_PYTHON=py"
if exist ".venv\Scripts\python.exe" set "FR5_PYTHON=.venv\Scripts\python.exe"

set "FR5_ROBOT_IP=192.168.58.2"
set /p "FR5_ROBOT_IP_INPUT=Robot IP [192.168.58.2]: "
if defined FR5_ROBOT_IP_INPUT set "FR5_ROBOT_IP=%FR5_ROBOT_IP_INPUT%"

choice /M "Allow Play, Pause, and Stop to control the physical FR5"
if errorlevel 2 goto readonly

"%FR5_PYTHON%" "fr5_connector.py" --robot-ip "%FR5_ROBOT_IP%" --allow-motion --results-dir "runs\fr5-live"
goto finished

:readonly
"%FR5_PYTHON%" "fr5_connector.py" --robot-ip "%FR5_ROBOT_IP%" --results-dir "runs\fr5-live"

:finished
echo.
echo FR5 Connector closed.
pause
