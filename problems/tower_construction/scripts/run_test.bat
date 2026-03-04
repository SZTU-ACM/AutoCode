@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo 正在设置开发环境...
python scripts\setup_dev.py
if errorlevel 1 (
    echo 设置失败！
    pause
    exit /b 1
)
echo.
echo 正在运行对拍测试...
python stress.py
pause
