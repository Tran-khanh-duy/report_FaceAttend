@echo off
setlocal
title FaceAttend Setup

echo.
echo ========================================================
echo   FACEATTEND AI - QUICK SETUP FOR NEW PC
echo ========================================================
echo.

:: 1. Kiem tra Python
echo [*] Checking Python...
python --version >nul 2>&1
if "%errorlevel%"=="0" set "PY_CMD=python" & goto :START_VENV

py --version >nul 2>&1
if "%errorlevel%"=="0" set "PY_CMD=py" & goto :START_VENV

echo ERROR: Python not found! Please install Python 3.10+
echo (Link: https://www.python.org/downloads/)
pause
exit /b 1

:START_VENV
echo [OK] Python found: %PY_CMD%

:: 2. Tao moi truong ao
echo [*] Creating virtual environment (.venv)...
if exist ".venv\Scripts\activate.bat" goto :INSTALL_REQS
"%PY_CMD%" -m venv .venv
if not exist ".venv\Scripts\activate.bat" echo ERROR: Cannot create .venv & pause & exit /b 1

:INSTALL_REQS
:: 3. Cai dat thu vien
echo [*] Installing libraries (Please wait 3-5 mins)...
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip

:: Kiem tra va cai dat Torch rieng de dam bao on dinh
echo [*] Checking PyTorch...
python -c "import torch" >nul 2>&1
if "%errorlevel%" neq "0" (
    echo [!] PyTorch not found. Installing standard version...
    pip install torch torchvision easydict --index-url https://download.pytorch.org/whl/cpu
)

echo [*] Installing other requirements...
pip install -r requirements.txt
if "%errorlevel%" neq "0" echo ERROR: Library installation failed! & pause & exit /b 1

:: 4. Tai Models
echo [*] Checking AI models...
call ".venv\Scripts\activate.bat"
if exist "download_anti_spoof_models.py" (
    python "download_anti_spoof_models.py"
) else (
    echo ERROR: Cannot find download_anti_spoof_models.py
)

:: 5. Kiem tra GPU (Tuy chon)
echo [*] Testing AI Engine and GPU...
if exist "check_gpu.py" python "check_gpu.py"

echo.
echo ========================================================
echo   SUCCESS! The system is ready on this PC.
echo   1. Edit Server IP in '.env.edge'
echo   2. Run 'run_edge.bat' to start the system.
echo   - Tip: If you have an NVIDIA GPU, make sure CUDA is 
echo     ready to enable High Performance mode.
echo ========================================================
echo.
pause
