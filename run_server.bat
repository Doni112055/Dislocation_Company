@echo off
chcp 65001 > nul
title Сервер Дислокация PRO (FastAPI)
echo ========================================================
echo   Запуск корпоративного сервера мониторинга контейнеров
echo ========================================================
echo.
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload
if %errorlevel% neq 0 (
    echo.
    echo Ошибка при запуске. Попытка запустить через py...
    py -m uvicorn server:app --host 0.0.0.0 --port 8000
)
pause
