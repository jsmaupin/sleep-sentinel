@echo off
setlocal
if not defined LOCALAPPDATA (
    echo LOCALAPPDATA is not set. Cannot locate a machine-local environment. 1>&2
    exit /b 1
)
set "UV_PROJECT_ENVIRONMENT=%LOCALAPPDATA%\Sleep-Sentinel\venv"
set "PYTHONDONTWRITEBYTECODE=1"
pushd "%~dp0"
if errorlevel 1 exit /b 1
uv %*
set "sentinel_exit=%ERRORLEVEL%"
popd
exit /b %sentinel_exit%
