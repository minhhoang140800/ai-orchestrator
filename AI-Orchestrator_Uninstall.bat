@echo off
setlocal EnableExtensions
title AI Orchestrator - Go cai dat
set "APPDIR=%LOCALAPPDATA%\AIOrchestrator"
echo.
echo   AI Orchestrator - GO CAI DAT
echo   Thu muc ket qua (noi luu file export) luon duoc giu nguyen.
echo.
echo   [1] Go app + dang xuat 3 AI          (de test lai tu buoc cai dat va dang nhap)
echo   [2] Muc 1 + go Claude Code, Codex, Antigravity
echo   [3] Muc 2 + go Git, Python, Node.js  (phan mem khac tren may co the dang dung)
echo   [0] Thoat
echo.
choice /c 1230 /n /m "Chon muc (1/2/3/0): "
set "LV=%errorlevel%"
if "%LV%"=="4" exit /b 0
choice /c YN /n /m "Xoa ca lich su task trong app? (Y/N): "
set "DELDATA=%errorlevel%"
echo.

echo [1] Dung app dang chay...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -match 'AIOrchestrator|launcher\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

echo [1] Dang xuat Claude, ChatGPT...
set "PATH=%PATH%;%USERPROFILE%\.local\bin;%APPDATA%\npm;%LOCALAPPDATA%\agy\bin"
where claude >nul 2>&1 && call claude auth logout >nul 2>&1
where codex >nul 2>&1 && call codex logout >nul 2>&1

echo [1] Dang xuat Gemini (xoa phien luu trong Windows Credential Manager)...
cmdkey /delete:gemini:antigravity >nul 2>&1
cmdkey /delete:LegacyGeneric:target=gemini:antigravity >nul 2>&1
if exist "%USERPROFILE%\.gemini\antigravity-cli" rd /s /q "%USERPROFILE%\.gemini\antigravity-cli"
if exist "%USERPROFILE%\.gemini\google_accounts.json" del /q "%USERPROFILE%\.gemini\google_accounts.json"
if exist "%USERPROFILE%\.gemini\oauth_creds.json" del /q "%USERPROFILE%\.gemini\oauth_creds.json"

echo [1] Go app va shortcut...
del /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\AI Orchestrator.lnk" >nul 2>&1
del /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AI Orchestrator.lnk" >nul 2>&1
del /q "%USERPROFILE%\Desktop\AI Orchestrator.lnk" >nul 2>&1
if "%DELDATA%"=="1" (
  if exist "%APPDIR%" rd /s /q "%APPDIR%"
) else (
  del /q "%APPDIR%\app.py" "%APPDIR%\app.py.new" "%APPDIR%\version.txt" "%APPDIR%\icon.ico" "%APPDIR%\install.log" >nul 2>&1
  if exist "%APPDIR%\data\config.json" del /q "%APPDIR%\data\config.json"
)
if "%LV%"=="1" goto done

echo [2] Go Codex...
where npm >nul 2>&1 && call npm uninstall -g @openai/codex >nul 2>&1
echo [2] Go Claude Code...
del /q "%USERPROFILE%\.local\bin\claude.exe" >nul 2>&1
if exist "%USERPROFILE%\.local\share\claude" rd /s /q "%USERPROFILE%\.local\share\claude"
echo [2] Go Antigravity CLI...
if exist "%LOCALAPPDATA%\agy" rd /s /q "%LOCALAPPDATA%\agy"
del /q "%USERPROFILE%\.local\bin\agy.exe" >nul 2>&1
if "%LV%"=="2" goto done

echo [3] Go Git, Python, Node.js...
where winget >nul 2>&1
if errorlevel 1 (
  echo     May khong co winget: vao Settings ^> Apps ^> Installed apps de go Git, Python 3.12, Node.js.
  goto done
)
winget uninstall -e --id Git.Git --silent
winget uninstall -e --id Python.Python.3.12 --silent
winget uninstall -e --id OpenJS.NodeJS.LTS --silent

:done
echo.
echo   XONG. De cai lai: bam dup file AI-Orchestrator_v... .bat
echo.
pause
