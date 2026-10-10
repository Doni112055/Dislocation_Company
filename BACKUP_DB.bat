@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "backups" mkdir "backups"
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value 2>nul') do set dt=%%I
if defined dt (
    set YYYY=%dt:~0,4%
    set MM=%dt:~4,2%
    set DD=%dt:~6,2%
    set HH=%dt:~8,2%
    set Min=%dt:~10,2%
    set Stamp=%YYYY%-%MM%-%DD%_%HH%-%Min%
) else (
    set Stamp=%date%
)
if exist "dislocation.db" (
    copy /y "dislocation.db" "backups\dislocation_%Stamp%.db" >nul
    echo =========================================================
    echo [УСПЕХ] Резервная копия базы создана в папке backups:
    echo backups\dislocation_%Stamp%.db
    echo =========================================================
) else (
    echo [ОШИБКА] Файл базы данных dislocation.db не найден.
)
timeout /t 3 >nul
