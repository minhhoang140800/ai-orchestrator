@echo off
setlocal EnableExtensions
title Maestro - Go cai dat
set "APPDIR=%LOCALAPPDATA%\AIOrchestrator"
echo.
echo   Maestro - GO CAI DAT
echo.
echo   [1] Go toan bo, GIU data   (app, shortcut; Claude Code / Codex / Antigravity chi go neu do Maestro cai;
echo                               giu cai dat, lich su task, bai hoc va file ket qua)
echo   [2] Go toan bo va XOA data (nhu [1] + xoa cai dat, lich su task, bai hoc; hoi rieng ve file ket qua)
echo   [0] Thoat
echo.
choice /c 120 /n /m "Chon muc (1/2/0): "
set "LV=%errorlevel%"
if "%LV%"=="3" exit /b 0
if "%LV%"=="2" (
  echo.
  choice /c YN /n /m "  Chac chan XOA data (cai dat, lich su task)? Khong khoi phuc duoc. (Y/N): "
  if errorlevel 2 exit /b 0
)
echo.

rem --- thu muc nhan ket qua (doc tu cai dat truoc khi xoa)
set "OUTDIR="
for /f "usebackq delims=" %%O in (`powershell -NoProfile -Command "try { (Get-Content -Raw '%APPDIR%\data\config.json' | ConvertFrom-Json).output } catch {}"`) do set "OUTDIR=%%O"

echo Dung Maestro dang chay...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -match 'AIOrchestrator|launcher\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

rem --- chi dang xuat / go Claude Code, Codex, Antigravity neu do Maestro cai (ghi trong installed_by_maestro.txt);
rem     ban nguoi dung tu cai tu truoc thi giu nguyen ca phan mem lan dang nhap
set "MINE=%APPDIR%\installed_by_maestro.txt"
set "GO_CLAUDE=" & set "GO_CODEX=" & set "GO_AGY="
if exist "%MINE%" (
  findstr /x /c:"Claude Code" "%MINE%" >nul 2>&1 && set "GO_CLAUDE=1"
  findstr /x /c:"ChatGPT (Codex)" "%MINE%" >nul 2>&1 && set "GO_CODEX=1"
  findstr /x /c:"Gemini (Antigravity)" "%MINE%" >nul 2>&1 && set "GO_AGY=1"
)
set "PATH=%PATH%;%USERPROFILE%\.local\bin;%APPDATA%\npm;%LOCALAPPDATA%\agy\bin"
echo Dang xuat cac AI do Maestro cai...
if defined GO_CLAUDE where claude >nul 2>&1 && call claude auth logout >nul 2>&1
if defined GO_CODEX where codex >nul 2>&1 && call codex logout >nul 2>&1
if defined GO_AGY (
  cmdkey /delete:gemini:antigravity >nul 2>&1
  cmdkey /delete:LegacyGeneric:target=gemini:antigravity >nul 2>&1
  if exist "%USERPROFILE%\.gemini\antigravity-cli" rd /s /q "%USERPROFILE%\.gemini\antigravity-cli"
  del /q "%USERPROFILE%\.gemini\google_accounts.json" "%USERPROFILE%\.gemini\oauth_creds.json" >nul 2>&1
)

echo Go shortcut va tu chay khi bat may...
for %%L in ("Maestro.lnk" "AI Orchestrator.lnk") do (
  del /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\%%~L" >nul 2>&1
  del /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\%%~L" >nul 2>&1
  del /q "%USERPROFILE%\Desktop\%%~L" >nul 2>&1
)

echo Go Claude Code, Codex, Antigravity (chi ban do Maestro cai)...
if defined GO_CODEX where npm >nul 2>&1 && call npm uninstall -g @openai/codex >nul 2>&1
if defined GO_CLAUDE (
  del /q "%USERPROFILE%\.local\bin\claude.exe" >nul 2>&1
  if exist "%USERPROFILE%\.local\share\claude" rd /s /q "%USERPROFILE%\.local\share\claude"
)
if defined GO_AGY (
  del /q "%USERPROFILE%\.local\bin\agy.exe" >nul 2>&1
  if exist "%LOCALAPPDATA%\agy" rd /s /q "%LOCALAPPDATA%\agy"
)
if not exist "%MINE%" echo   (Ban cai truoc v10.6 khong ghi lai phan mem nao do Maestro cai: giu nguyen Claude Code / Codex / Antigravity.)

echo Go app...
del /q "%APPDIR%\app.py" "%APPDIR%\app.py.new" "%APPDIR%\app.py.old" "%APPDIR%\version.txt" "%APPDIR%\icon.ico" >nul 2>&1
del /q "%APPDIR%\current.txt" "%APPDIR%\next.txt" "%APPDIR%\node_dir.txt" >nul 2>&1
for %%D in (versions __pycache__ data\.login) do if exist "%APPDIR%\%%D" rd /s /q "%APPDIR%\%%D"
if "%LV%"=="1" goto keep

echo Xoa data: cai dat, lich su task, bai hoc...
if exist "%APPDIR%" rd /s /q "%APPDIR%"
if defined OUTDIR if exist "%OUTDIR%\_AI-Orchestrator" rd /s /q "%OUTDIR%\_AI-Orchestrator"
if not defined OUTDIR goto done
if not exist "%OUTDIR%" goto done
if "%OUTDIR:~3%"=="" goto done
rem --- thu muc chung cua Windows (Desktop, Documents, Downloads, OneDrive, thu muc nguoi dung): KHONG xoa ca thu muc
set "SHARED="
for /f "usebackq delims=" %%S in (`powershell -NoProfile -Command "$o=[IO.Path]::GetFullPath($env:OUTDIR).TrimEnd('\'); $l=@([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('MyDocuments'),[Environment]::GetFolderPath('UserProfile'),(Join-Path $env:USERPROFILE 'Downloads'),$env:OneDrive,$env:PUBLIC) | Where-Object {$_} | ForEach-Object {$_.TrimEnd('\')}; if ($l -contains $o) {'1'}"`) do set "SHARED=%%S"
if defined SHARED (
  echo.
  echo   File ket qua nam trong thu muc chung cua Windows: %OUTDIR%
  echo   Khong xoa ca thu muc nay. Neu muon, hay tu xoa cac thu muc du an ben trong.
  goto done
)
echo.
echo   File ket qua (san pham) cua ban nam o: %OUTDIR%
choice /c NY /n /m "  Xoa ca file ket qua nay? Mac dinh KHONG. (N = giu / Y = xoa): "
if errorlevel 2 (
  echo   Xoa file ket qua...
  rd /s /q "%OUTDIR%"
) else (
  echo   Giu nguyen file ket qua.
)
goto done

:keep
echo Giu data: %APPDIR%\data va lich su task trong thu muc ket qua.

:done
echo.
echo   XONG. Git, Python, Node.js van giu (phan mem khac tren may co the dang dung).
echo   De cai lai: bam dup Maestro_m_setup.bat
echo.
pause
