@echo off
rem Double-cliquez sur ce fichier pour lancer VintedBot sous Windows.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
set PY=python
where py >nul 2>nul && set PY=py -3
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo.
  echo Python n'est pas installe.
  echo Telechargez-le sur https://www.python.org/downloads/
  echo IMPORTANT : cochez "Add python.exe to PATH" pendant l'installation.
  echo.
  pause
  exit /b 1
)
%PY% -m vintedbot setup --if-needed
if errorlevel 1 (
  pause
  exit /b 1
)
echo.
echo VintedBot demarre. Laissez cette fenetre ouverte (fermez-la pour arreter le bot).
echo.
%PY% -m vintedbot run
pause
