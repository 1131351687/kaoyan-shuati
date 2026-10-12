@echo off
chcp 65001 >nul
title 考研刷题
cd /d "%~dp0"

echo.
echo   ============================================
echo    考研刷题
echo    1823 题 / 1987-2026 / 数学一·二·三 + 模拟卷
echo   ============================================
echo.
echo   正在启动本地服务...
echo.

REM 服务入口在 app\ 下，数据在 data\ 下（bank.py 内部按脚本位置定位）
REM 延迟 1 秒再开浏览器，让服务先起来
start "" cmd /c "timeout /t 1 >nul & start "" http://127.0.0.1:8899/"

REM 逐级探测 Python：不写死任何盘符
REM   1) 环境变量 ZTQ_PYTHON
REM   2) py 启动器（Windows 官方，优先级最高）
REM   3) PATH 上的 python
REM   4) 常见安装位置
set PY=
if defined ZTQ_PYTHON if exist "%ZTQ_PYTHON%" set PY=%ZTQ_PYTHON%

if not defined PY (
  where py >nul 2>nul && set PY=py
)
if not defined PY (
  where python >nul 2>nul && set PY=python
)
if not defined PY (
  for %%D in (
    "%LOCALAPPDATA%\Programs\Python\Python313"
    "%LOCALAPPDATA%\Programs\Python\Python312"
    "%LOCALAPPDATA%\Programs\Python\Python311"
    "%LOCALAPPDATA%\Programs\Python\Python310"
    "C:\Python313"
    "C:\Python312"
    "C:\Python311"
    "D:\miniconda"
  ) do (
    if not defined PY if exist "%%~D\python.exe" set PY=%%~D\python.exe
  )
)

if not defined PY (
  echo.
  echo   [错误] 没有找到 Python。
  echo.
  echo   请任选一种方式解决：
  echo     1. 安装 Python 3.10+ 并勾选 "Add to PATH"
  echo         https://www.python.org/downloads/
  echo     2. 设置环境变量 ZTQ_PYTHON 指向你的 python.exe
  echo.
  pause
  exit /b 1
)

echo   使用解释器: %PY%
echo.
"%PY%" "%~dp0app\bank.py" serve --port 8899

echo.
echo   服务已停止。
pause
