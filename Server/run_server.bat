@echo off
setlocal
title FaceAttend SERVER
color 0b

echo ============================================================
echo           FACEATTEND SERVER - QUAN LY TRUNG TAM
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
if not exist "assets\snapshots" mkdir assets\snapshots
if not exist "reports\output" mkdir reports\output

:: 4. Khoi dong he thong ngay lap tuc
echo [INFO] Dang kiem tra cau hinh...
if not exist ".env.server" (
    echo [CANH BAO] Khong tim thay file .env.server! Dang dung mac dinh.
)

echo.
echo [1/3] Dang khoi dong ha tang (Redis, MySQL) qua Docker...
docker-compose up -d redis db >nul 2>&1
if %errorlevel% neq 0 (
    docker compose up -d redis db >nul 2>&1
    if %errorlevel% neq 0 (
        echo [CANH BAO] Khong the chay Docker. Vui long chac chan Docker Desktop da duoc bat!
    ) else (
        echo [OK] Da chay Redis/DB qua 'docker compose'
    )
) else (
    echo [OK] Da chay Redis/DB qua 'docker-compose'
)

echo.
echo [2/3] Dang khoi dong API Server (Port: 8000)...
start "FaceAttend API Server" cmd /k "title [SERVER] API Server && %PY_EXE% api_server.py"

:: Cho API Server khoi dong (5 giay)
timeout /t 5 /nobreak >nul

echo.
echo [3/3] Dang khoi dong Giao dien quan ly chinh (Dashboard)...
%PY_EXE% main.py

echo.
echo ============================================================
echo [DONE] Dashboard da dong hoac xay ra loi. Nhan phim bat ky de thoat.
echo ============================================================
pause
exit
