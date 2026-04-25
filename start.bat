@echo off
setlocal EnableExtensions
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

echo ==========================================
echo   URP 抢课助手 - 一键启动
echo ==========================================

set "INIT_ONLY=0"
if /I "%~1"=="--init-only" set "INIT_ONLY=1"

where uv >nul 2>nul
if %errorlevel%==0 (
    echo [1/2] 检测到 uv，正在检查依赖...
    uv sync
    if errorlevel 1 (
        echo.
        echo 依赖安装失败，请检查网络后重试。
        echo 将自动回退到 Python/.venv 方式...
    ) else (
        if "%INIT_ONLY%"=="1" (
            echo [2/2] 环境准备完成（未启动服务）。
            goto :end
        )
        echo [2/2] 正在启动服务...
        uv run .\app.py
        goto :end
    )
)

set "PY_CMD="
where py >nul 2>nul
if %errorlevel%==0 set "PY_CMD=py -3"

if not defined PY_CMD (
    where python >nul 2>nul
    if %errorlevel%==0 set "PY_CMD=python"
)

if not defined PY_CMD (
    echo 未检测到 Python（py 或 python 命令）。
    echo 请先安装 Python 3.14+，并勾选 "Add Python to PATH"。
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] 正在创建 .venv 虚拟环境...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo 创建 .venv 失败，请检查 Python 安装后重试。
        pause
        exit /b 1
    )
) else (
    echo [1/4] 检测到已存在的 .venv。
)

echo [2/4] 正在升级 pip...
.\.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 (
    echo pip 升级失败，请检查网络后重试。
    pause
    exit /b 1
)

echo [3/4] 正在安装依赖...
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
)

if "%INIT_ONLY%"=="1" (
    echo [4/4] 环境准备完成（未启动服务）。
    goto :end
)

echo [4/4] 正在启动服务...
.\.venv\Scripts\python.exe .\app.py

:end
echo.
echo 程序已退出（如果是手动关闭窗口或 Ctrl+C，属于正常）。
pause
endlocal


