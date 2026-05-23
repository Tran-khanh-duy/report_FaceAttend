@echo off
setlocal
title FaceAttend EDGE BOX
color 0a

echo ============================================================
echo           FACEATTEND EDGE BOX - DIEM DANH KHUON MAT
echo ============================================================
echo.

:: 1. Kiem tra Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [LOI] Khong tim thay Python! Vui long cai dat Python 3.10+.
    pause
    exit /b
)

:: 2. Thiet lap Python Executable
set PY_EXE=python
if exist .venv\Scripts\python.exe (
    set PY_EXE=.venv\Scripts\python.exe
) else if exist venv\Scripts\python.exe (
    set PY_EXE=venv\Scripts\python.exe
)

:: 3. Tao cac thu muc can thiet
if not exist "models" mkdir models
if not exist "logs" mkdir logs
if not exist "database" mkdir database

:: 4. Khoi dong he thong ngay lap tuc
echo [INFO] Dang kiem tra cau hinh...
if not exist ".env.edge" (
    echo [CANH BAO] Khong tim thay file .env.edge!
)

echo.
echo ============================================================
echo   DANG KHOI DONG EDGE BOX (Nhan Ctrl+C de dung)...
echo ============================================================
echo.
%PY_EXE% main_edge.py

echo.
echo ============================================================
echo [DONE] Edge Box da dung.
echo ============================================================
pause
exit
