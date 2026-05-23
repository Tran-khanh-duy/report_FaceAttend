@echo off
title FaceAttend AI - Setup
color 0a

echo ============================================================
echo      KHOI TAO MOI TRUONG CHO FACEATTEND AI
echo ============================================================
echo.

:: 1. Tao moi truong ao .venv
if not exist .venv (
    echo [1/2] Dang tao moi truong ao .venv...
    python -m venv .venv
)

:: 2. Kich hoat va cai dat libraries
echo [2/2] Dang cai dat cac thu vien tu requirements.txt...
echo [HINT] Qua trinh nay co the mat vai phut tuy vao bang thong.
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo ============================================================
echo [XONG] Da cai dat moi truong! Hay chay file run_project.bat.
echo ============================================================
pause
