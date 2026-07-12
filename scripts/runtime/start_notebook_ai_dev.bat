@echo off
setlocal

set "ROOT=D:\LEARNING\Tools\notebook_ai"
set "PYTHON_EXE=D:\LEARNING\Tools\ANACONDA\envs\NOTEBOOK_AI\python.exe"

echo NOTEBOOK_AI dev launcher
echo.

if not exist "%ROOT%\" (
    echo [ERROR] Project directory not found:
    echo   %ROOT%
    goto :fail
)

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python executable not found:
    echo   %PYTHON_EXE%
    goto :fail
)

if not exist "%ROOT%\frontend\package.json" (
    echo [ERROR] frontend/package.json not found:
    echo   %ROOT%\frontend\package.json
    goto :fail
)

if not exist "%ROOT%\app\main.py" (
    echo [ERROR] app/main.py not found:
    echo   %ROOT%\app\main.py
    goto :fail
)

where npm.cmd >nul 2>nul
if errorlevel 1 (
    echo [ERROR] npm.cmd was not found on PATH.
    echo Install/check Node.js separately; this launcher does not modify PATH or install dependencies.
    goto :fail
)

echo Starting backend window...
start "NOTEBOOK_AI backend" /D "%ROOT%" cmd /k ""%PYTHON_EXE%" -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo Starting frontend window...
start "NOTEBOOK_AI frontend" /D "%ROOT%" cmd /k "npm.cmd --prefix frontend run dev"

echo.
echo NOTEBOOK_AI dev servers are starting.
echo Backend:        http://127.0.0.1:8000
echo Backend health: http://127.0.0.1:8000/health
echo Frontend:       http://127.0.0.1:5173
echo.
echo If the Chapter 8 note-correction fields look stale or missing, run:
echo   "%PYTHON_EXE%" -B scripts\check_notebook_ai_dev_status.py
echo If the checker says the backend may not have loaded latest code,
echo close both backend/frontend windows and run this launcher again.
echo.
echo To stop: close the backend and frontend command windows.
echo Closing the two windows is the intended stop method; this launcher does not kill old processes.
echo This launcher does not write DB, run imports, call LLMs, install dependencies,
echo or modify PATH, registry, or system environment variables.
echo.

if "%NOTEBOOK_AI_NO_PAUSE%"=="1" goto :done
pause
goto :done

:fail
echo.
echo Startup checks failed. No dev server windows were launched.
if "%NOTEBOOK_AI_NO_PAUSE%"=="1" exit /b 1
pause
exit /b 1

:done
endlocal
