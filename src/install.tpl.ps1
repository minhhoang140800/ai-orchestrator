$ErrorActionPreference = 'Continue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$M = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__MSG_B64__')) | ConvertFrom-Json
$Version = '__VER__'
$AppHome = Join-Path $env:LOCALAPPDATA 'AIOrchestrator'
$Log = Join-Path $AppHome 'install.log'
$AppPy = Join-Path $AppHome 'app.py'
$VerFile = Join-Path $AppHome 'version.txt'
New-Item -ItemType Directory -Force -Path $AppHome | Out-Null
Add-Content -Path $Log -Value ("`n==== " + (Get-Date) + " ====") -Encoding UTF8

function Say($t, $c) { if (-not $c) { $c = 'Gray' }; Write-Host $t -ForegroundColor $c; Add-Content -Path $Log -Value $t -Encoding UTF8 }
function Line($label, $state, $color) {
  Write-Host ('  ' + $label.PadRight(26)) -NoNewline
  Write-Host $state -ForegroundColor $color
  Add-Content -Path $Log -Value ($label + ' : ' + $state) -Encoding UTF8
}
function Has($c) { return [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function RefreshPath {
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + $env:USERPROFILE + '\.local\bin;' + $env:APPDATA + '\npm;' + $env:LOCALAPPDATA + '\Microsoft\WindowsApps'
}
function FindPy {
  $code = 'import sys;print(sys.executable)'
  foreach ($exe in @('py', 'python')) {
    try {
      if ($exe -eq 'py') { $p = & py -3 -c $code 2>$null } else { $p = & python -c $code 2>$null }
      if ($LASTEXITCODE -eq 0 -and $p) { $p = ([string]$p).Trim(); if (Test-Path $p) { return $p } }
    } catch {}
  }
  return $null
}
function Step($label, [scriptblock]$check, [scriptblock]$install) {
  if (& $check) { Line $label $M.have 'DarkGray'; return $true }
  Line $label $M.installing 'Yellow'
  try { & $install *>> $Log } catch { Add-Content -Path $Log -Value $_.Exception.Message -Encoding UTF8 }
  RefreshPath
  if (& $check) { Line $label $M.done 'Green'; return $true }
  Line $label $M.failed 'Red'
  return $false
}
function Fail($text) {
  Say ''
  Say $text 'Red'
  Say ($M.log + ' ' + $Log) 'Red'
  exit 1
}
function AppUp {
  foreach ($p in @(80, 8765)) {
    try { $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$p/icon.svg" -TimeoutSec 2; if ($r.Content -match '<svg') { return $true } } catch {}
  }
  return $false
}
function AppLink {
  $p = 80
  try { $c = Get-Content (Join-Path $AppHome 'data\config.json') -Raw | ConvertFrom-Json; if ($c.port) { $p = [int]$c.port } } catch {}
  if ($p -eq 80) { return 'http://ai-orchestrator.localhost/' }
  return "http://ai-orchestrator.localhost:$p/"
}
function PyW {
  $py = FindPy
  if (-not $py) { return $null }
  $w = Join-Path (Split-Path $py) 'pythonw.exe'
  if (Test-Path $w) { return $w }
  return $py
}

RefreshPath
# Da cai ban bang hoac moi hon (app tu cap nhat tu GitHub) -> chi mo, khong cai de ban cu len
$installed = $false
if ((Test-Path $AppPy) -and (Test-Path $VerFile)) {
  $iv = (Get-Content $VerFile -Raw).Trim()
  $installed = ($iv -match '^\d{8}\.\d{4}-') -and ([string]::CompareOrdinal($iv, $Version) -ge 0)
}

# ---------- Đã cài đúng phiên bản: chỉ mở ----------
if ($installed -and (Has claude) -and (PyW)) {
  Say ('  ' + $M.opening) 'Cyan'
  $pyw = PyW
  Start-Process -FilePath $pyw -ArgumentList ('"' + $AppPy + '"') -WorkingDirectory $AppHome
  for ($i = 0; $i -lt 20 -and -not (AppUp); $i++) { Start-Sleep -Milliseconds 500 }
  if (-not (AppUp)) { Fail $M.no_start }
  Say ('  ' + (AppLink)) 'Cyan'
  Start-Sleep -Seconds 2
  exit 0
}

# ---------- Cài mới / cập nhật ----------
Say ''
Say ('  ' + $M.title) 'Green'
if (Test-Path $AppPy) { Say ('  ' + $M.mode_update) 'Cyan' } else { Say ('  ' + $M.mode_fresh) 'Cyan' }
Say ('  ' + $M.uac) 'DarkGray'
Say ''

# winget: co san thi dung; thieu thi thu kich hoat; van thieu thi tai thang bo cai tu trang chu
if (-not (Has winget)) {
  try { Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe -ErrorAction Stop *>> $Log } catch {}
  RefreshPath
  $wa = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps'
  if (-not (Has winget) -and (Test-Path (Join-Path $wa 'winget.exe'))) { $env:Path += ';' + $wa }
}
$UseWinget = Has winget
if ($UseWinget) { Line 'winget' $M.have 'DarkGray' } else { Line 'winget' $M.no_winget 'DarkGray' }

function Download($url, $name) {
  $f = Join-Path $env:TEMP $name
  Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $f
  return $f
}
function InstallGitDirect {
  $rel = Invoke-RestMethod -UseBasicParsing 'https://api.github.com/repos/git-for-windows/git/releases/latest'
  $asset = $rel.assets | Where-Object { $_.name -match '^Git-[\d.]+-64-bit\.exe$' } | Select-Object -First 1
  $f = Download $asset.browser_download_url 'git-setup.exe'
  Start-Process -FilePath $f -ArgumentList '/VERYSILENT', '/NORESTART', '/NOCANCEL', '/SP-', '/CURRENTUSER' -Wait
}
function InstallPythonDirect {
  $f = Download 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' 'python-setup.exe'
  Start-Process -FilePath $f -ArgumentList '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_launcher=1', 'InstallLauncherAllUsers=0', 'Include_test=0' -Wait
}
function InstallNodeDirect {
  $idx = Invoke-RestMethod -UseBasicParsing 'https://nodejs.org/dist/index.json'
  $v = ($idx | Where-Object { $_.lts } | Select-Object -First 1).version
  $f = Download "https://nodejs.org/dist/$v/node-$v-x64.msi" 'node-setup.msi'
  Start-Process -FilePath 'msiexec.exe' -ArgumentList '/i', ('"' + $f + '"'), '/passive', '/norestart' -Wait
}

# Dừng bản đang chạy (cả bản cũ) để cập nhật
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -like 'python*' -and $_.CommandLine -and ($_.CommandLine -match 'launcher\.py' -or $_.CommandLine -match 'AIOrchestrator') } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$wg = @('install', '-e', '--silent', '--accept-source-agreements', '--accept-package-agreements', '--id')
$null = Step 'Git' { Has git } { if ($UseWinget) { & winget @wg 'Git.Git' }; if (-not (Has git)) { InstallGitDirect } }
$ok = Step 'Python' { [bool](FindPy) } { if ($UseWinget) { & winget @wg 'Python.Python.3.12' '--scope' 'user' }; RefreshPath; if (-not (FindPy)) { InstallPythonDirect } }
if (-not $ok) { Fail $M.no_python }
$null = Step 'Node.js' { Has node } { if ($UseWinget) { & winget @wg 'OpenJS.NodeJS.LTS' }; RefreshPath; if (-not (Has node)) { InstallNodeDirect } }
$ok = Step 'Claude Code' { Has claude } { & powershell.exe -NoProfile -ExecutionPolicy Bypass -Command 'Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression' }
if (-not $ok) { Fail $M.no_claude }
$null = Step 'ChatGPT (Codex)' { Has codex } { & npm.cmd install -g '@openai/codex' }
$null = Step 'Gemini (Antigravity)' { Has agy } { & powershell.exe -NoProfile -ExecutionPolicy Bypass -Command 'Invoke-RestMethod https://antigravity.google/cli/install.ps1 | Invoke-Expression' }

$py = FindPy
Line $M.libs $M.installing 'Yellow'
& $py -m pip install --user --upgrade --quiet --disable-pip-version-check pillow openpyxl *>> $Log

[IO.File]::WriteAllBytes($AppPy, [Convert]::FromBase64String('__APP_B64__'))
[IO.File]::WriteAllBytes((Join-Path $AppHome 'icon.ico'), [Convert]::FromBase64String('__ICO_B64__'))
$pyw = PyW

# Start Menu (mở) + Startup (chạy nền khi bật máy để task tự chạy tiếp). Không tạo icon Desktop.
$oldLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'AI Orchestrator.lnk'
if (Test-Path $oldLnk) { Remove-Item $oldLnk -Force }
$ws = New-Object -ComObject WScript.Shell
$lnkMenu = Join-Path ([Environment]::GetFolderPath('Programs')) 'AI Orchestrator.lnk'
$lnkStart = Join-Path ([Environment]::GetFolderPath('Startup')) 'AI Orchestrator.lnk'
foreach ($pair in @(@($lnkMenu, ('"' + $AppPy + '"')), @($lnkStart, ('"' + $AppPy + '" --background')))) {
  $s = $ws.CreateShortcut($pair[0])
  $s.TargetPath = $pyw
  $s.Arguments = $pair[1]
  $s.WorkingDirectory = $AppHome
  $s.IconLocation = (Join-Path $AppHome 'icon.ico') + ',0'
  $s.Save()
}
Set-Content -Path $VerFile -Value $Version -Encoding ASCII
Line $M.app $M.done 'Green'

Start-Process -FilePath $pyw -ArgumentList ('"' + $AppPy + '"') -WorkingDirectory $AppHome
for ($i = 0; $i -lt 30 -and -not (AppUp); $i++) { Start-Sleep -Milliseconds 700 }
if (-not (AppUp)) { Fail $M.no_start }
Say ''
Say ('  ' + $M.ok) 'Green'
Say ('  ' + (AppLink)) 'Cyan'
Start-Sleep -Seconds 8
exit 0
