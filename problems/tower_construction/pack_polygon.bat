@echo off
chcp 65001 >nul
cd /d "c:\userProgram\ACMGO\problems\tower_construction"

echo 正在打包成 Polygon 格式...
python pack_polygon.py

echo.
pause
