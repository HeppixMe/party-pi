@echo off
setlocal enabledelayedexpansion
REM ---------------------------------------------------------------------
REM party-pi - Ein-Klick-Start fuer Windows.
REM
REM Sucht Python, startet den Server in einem eigenen Fenster und oeffnet
REM danach die Steuerseite im Standardbrowser. Kein Passwort, keine
REM Installation - einfach diese Datei doppelklicken.
REM
REM Port und Datenordner anpassen: die zwei Zeilen unten aendern.
REM ---------------------------------------------------------------------

set PORT=8000
set DATADIR=%~dp0data

title party-pi
cd /d "%~dp0"

echo.
echo   party-pi - LAN-Party-Leaderboard
echo   =================================
echo.

REM -- Python suchen (python, dann den py-Starter) ------------------------
set PY=
where python >nul 2>nul
if %errorlevel%==0 (
    set "PY=python"
) else (
    where py >nul 2>nul
    if !errorlevel!==0 (
        set "PY=py"
    )
)

if "%PY%"=="" (
    echo   Python wurde nicht gefunden.
    echo.
    echo   Bitte zuerst Python installieren:
    echo     https://www.python.org/downloads/
    echo.
    echo   Beim Installieren unbedingt das Haekchen bei
    echo   "Add python.exe to PATH" setzen - danach diese Datei
    echo   noch einmal doppelklicken.
    echo.
    pause
    exit /b 1
)

echo   Gefunden:
%PY% --version

if not exist "%DATADIR%" mkdir "%DATADIR%"

echo.
echo   Starte den Server in einem eigenen Fenster ...
echo   ( Das Fenster einfach offen lassen - schliessen beendet die Party. )
echo.
start "party-pi Server (bitte offen lassen)" cmd /k %PY% leaderboard.py --port %PORT% --data "%DATADIR%"

echo   Warte kurz, bis der Server bereit ist ...
timeout /t 2 /nobreak >nul

echo   Oeffne die Steuerung im Browser ...
start "" http://localhost:%PORT%/admin

echo.
echo   Fertig. Falls Windows nach der Firewall fragt: erlauben
echo   (nur fuer das eigene Netzwerk noetig, damit Handys mitmachen koennen).
echo.
echo   Andere Geraete im selben WLAN erreichen die Seite ueber die
echo   Adresse, die im Server-Fenster steht (z.B. http://192.168.1.23:%PORT%/).
echo.
pause
