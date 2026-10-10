@echo off
chcp 65001 >nul
title Мониторинг Контейнеров PRO — Сервер 24/7
echo =====================================================================
echo    ЗАПУСК КОРПОРАТИВНОГО СЕРВЕРА ДИСЛОКАЦИИ КОНТЕЙНЕРОВ PRO
echo =====================================================================
echo.

cd /d "%~dp0"

echo [1/3] Проверка окружения Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ОШИБКА] Python не обнаружен в системе!
    echo Пожалуйста, установите Python с сайта https://www.python.org/
    echo При установке обязательно поставьте галочку "Add Python to PATH"!
    echo.
    pause
    exit /b 1
)

echo [2/3] Проверка необходимых библиотек...
pip install fastapi uvicorn openpyxl --quiet

echo [3/3] Запуск сервера на http://0.0.0.0:8000 ...
echo Сервер доступен в локальной сети компании для всех сотрудников!
echo.

start http://127.0.0.1:8000

:LOOP
python server.py
echo.
echo [ВНИМАНИЕ] Процесс сервера завершился. Автоматический перезапуск через 3 секунды...
timeout /t 3 /nobreak >nul
goto LOOP
