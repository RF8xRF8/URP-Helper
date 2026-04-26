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
    echo [1/3] 检测到 uv，正在检查依赖...
    uv sync
    if errorlevel 1 (
        echo.
        echo 依赖安装失败，请检查网络后重试。
        pause
        exit /b 1
    ) else (
        echo [2/3] 正在检查 OCR 运行库...
        uv run python -c "import ddddocr" >nul 2>nul
        if errorlevel 1 (
            echo OCR运行库检查不通过。
            echo 亲，缺VC了，补补VC吧。
            echo Download: https://aka.ms/vs/17/release/vc_redist.x64.exe
            pause
            exit /b 1
        )
        if "%INIT_ONLY%"=="1" (
            echo [3/3] 环境准备完成（未启动服务）。
            goto :end
        )
        echo [3/3] 正在启动服务...
        uv run .\app.py
        goto :end
    )
)

echo 未检测到 uv，使用原生 .venv 方式...

set "PY_CMD="
where py >nul 2>nul
if %errorlevel%==0 set "PY_CMD=py -3"

if not defined PY_CMD (
    where python >nul 2>nul
    if %errorlevel%==0 set "PY_CMD=python"
)

if not defined PY_CMD (
    echo 未检测到 Python（py 或 python 命令）。
    echo Please install Python 3.14+ first.
    echo During installation, enable Add Python to PATH.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/5] 正在创建 .venv 虚拟环境...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo 创建 .venv 失败，请检查 Python 安装后重试。
        pause
        exit /b 1
    )
) else (
    echo [1/5] 检测到已存在的 .venv。
)

echo [2/5] 正在升级 pip...
.\.venv\Scripts\python.exe -m pip install --upgrade -q --disable-pip-version-check pip >nul 2>nul
if errorlevel 1 (
    echo pip 升级失败，请检查网络后重试。
    pause
    exit /b 1
)

if exist "pyproject.toml" (
    echo [3/5] 正在从 pyproject.toml 安装依赖...
    .\.venv\Scripts\python.exe -c "import pathlib, subprocess, sys, tomllib; data = tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8')); deps = data.get('project', {}).get('dependencies', []); sys.exit(0 if not deps else subprocess.call([sys.executable, '-m', 'pip', 'install', '-q', '--disable-pip-version-check', *deps]))" >nul 2>nul
) else (
    echo 未找到 pyproject.toml，无法安装依赖。
    pause
    exit /b 1
)
if errorlevel 1 (
    echo 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
)

echo [4/5] 正在检查 OCR 运行库...
.\.venv\Scripts\python.exe -c "import ddddocr" >nul 2>nul
if errorlevel 1 (
    echo OCR运行库检查不通过。
    echo 亲，缺VC了，补补VC吧。
    echo Download: https://aka.ms/vs/17/release/vc_redist.x64.exe
    pause
    exit /b 1
)

if "%INIT_ONLY%"=="1" (
    echo [5/5] 环境准备完成（未启动服务）。
    goto :end
)

echo [5/5] 正在启动服务...
.\.venv\Scripts\python.exe .\app.py

:end
echo.
echo 程序已退出（如果是手动关闭窗口或 Ctrl+C，属于正常）。
pause
endlocal


