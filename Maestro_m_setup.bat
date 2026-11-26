@echo off
setlocal EnableExtensions
title Maestro - Cai dat / cap nhat
echo.
echo   Maestro - CAI DAT
echo   File nay luon tai bo cai MOI NHAT tu GitHub roi chay, khong can thay file khi co ban moi.
echo.
set "AIO_BOOT=%TEMP%\Maestro_setup_latest.bat"
del /q "%AIO_BOOT%" >nul 2>&1
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue'; try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072 } catch {}; try { [Net.WebRequest]::DefaultWebProxy.Credentials = [Net.CredentialCache]::DefaultNetworkCredentials } catch {};" ^
  "$r='minhhoang140800/ai-orchestrator'; $f=$env:AIO_BOOT; $t=[DateTime]::Now.Ticks;" ^
  "$src=@(@('https://api.github.com/repos/'+$r+'/contents/Maestro_setup_latest.bat?ref=main','application/vnd.github.raw'), @('https://raw.githubusercontent.com/'+$r+'/main/Maestro_setup_latest.bat?t='+$t,''), @('https://cdn.jsdelivr.net/gh/'+$r+'@main/Maestro_setup_latest.bat',''));" ^
  "foreach ($s in $src) { try { $h=@{'User-Agent'='maestro'}; if ($s[1]) { $h['Accept']=$s[1] }; Invoke-WebRequest -UseBasicParsing -Uri $s[0] -Headers $h -OutFile $f -TimeoutSec 60; if ((Test-Path $f) -and (Get-Item $f).Length -gt 100000 -and (Select-String -Path $f -Pattern 'PS-BEGIN' -Quiet)) { Write-Host '  Da tai bo cai moi nhat.'; exit 0 } } catch {} };" ^
  "exit 1"
if errorlevel 1 (
  echo.
  echo   Khong tai duoc bo cai tu GitHub. Kiem tra mang Internet roi chay lai file nay.
  echo.
  pause
  exit /b 1
)
call "%AIO_BOOT%"
exit /b %errorlevel%
