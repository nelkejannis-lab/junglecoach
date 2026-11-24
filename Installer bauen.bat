@echo off
setlocal
title Jungle Coach - Installer bauen
cd /d "%~dp0"
echo.
echo === Jungle Coach: Installer wird gebaut ===
echo.

rem 1) Python pruefen / installieren
where python >nul 2>nul
if errorlevel 1 (
  echo [1/4] Python fehlt - wird ueber winget installiert...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  echo Bitte dieses Fenster schliessen und die Datei erneut starten.
  pause & exit /b
)
echo [1/4] Python gefunden.

rem 2) Abhaengigkeiten
echo [2/4] Installiere Abhaengigkeiten...
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt pyinstaller
if errorlevel 1 ( echo Fehler bei pip install. & pause & exit /b 1 )

rem 3) App bauen (Ordner-Variante: startet schneller, weniger Virenscanner-Fehlalarme)
echo [3/4] Baue App...
python -m PyInstaller --noconfirm --clean --windowed --name Moosi --icon assets\icon.ico ^
  --add-data "jc\static;jc\static" --add-data "config.json;." ^
  --collect-all webview --collect-all imageio_ffmpeg --collect-all pyaudiowpatch --collect-submodules jc run_app.py
if errorlevel 1 ( echo Fehler beim Bauen der App. & pause & exit /b 1 )

rem 4) Installer bauen (Inno Setup)
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo [4/4] Inno Setup fehlt - wird ueber winget installiert...
  winget install -e --id JRSoftware.InnoSetup --accept-package-agreements --accept-source-agreements
  set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
  if not exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
)
echo [4/4] Baue Installer...
"%ISCC%" /Q installer\JungleCoach.iss
if errorlevel 1 ( echo Fehler beim Installer. & pause & exit /b 1 )

echo.
echo === Fertig! ===
echo Installer: %cd%\dist\Moosi-Setup.exe
explorer dist
pause
