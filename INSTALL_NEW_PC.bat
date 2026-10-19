@echo off
setlocal EnableExtensions
if /I "%~1"=="--INSTALL-WORKER" goto :INSTALL_WORKER
echo [INFO] Opening installer window (will remain open after any error)...
cmd.exe /d /k call "%~f0" --INSTALL-WORKER
exit /b %errorlevel%

:INSTALL_WORKER
cd /d "%~dp0"
if errorlevel 1 goto :BAD_FOLDER
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONNOUSERSITE=1"
set "PIP_PROGRESS_BAR=off"
set "PIP_NO_COLOR=1"
set "PYTHONPATH="
set "PYTHONHOME="
set "PIP_TARGET="
set "PIP_PREFIX="
set "PIP_REQUIRE_VIRTUALENV="
set "INSTALL_SELF=%~f0"
title LaughDetectionV2 - Portable Installer - Reuse FFmpeg
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:INSTALL_SELF);$tag=':__PS_PAYLOAD__';$i=$s.LastIndexOf($tag);if($i -lt 0){throw 'Installer payload missing'}; & ([scriptblock]::Create($s.Substring($i+$tag.Length)))"
set "INSTALL_RC=%errorlevel%"
echo.
echo ==========================================================
if "%INSTALL_RC%"=="0" (echo [OK] Setup completed.) else (echo [ERROR] Setup failed with code %INSTALL_RC%.)
echo Log: "%~dp0install_new_pc.log"
echo This window remains open; type EXIT to close it.
echo ==========================================================
pause
exit /b %INSTALL_RC%

:BAD_FOLDER
echo [ERROR] Cannot open the project directory.
pause
exit /b 1

:__PS_PAYLOAD__
# Strict local, no system/user/registry Python and no parent folder scans.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONNOUSERSITE='1'
$env:PIP_PROGRESS_BAR='off'
$env:PIP_NO_COLOR='1'
$env:NO_COLOR='1'
$env:PYTHONPATH=''
$env:PYTHONHOME=''
$env:PIP_TARGET=''
$env:PIP_PREFIX=''
$env:PIP_REQUIRE_VIRTUALENV=''
try {
    $enc=New-Object System.Text.UTF8Encoding($false)
    [Console]::OutputEncoding=$enc
    $OutputEncoding=$enc
} catch { }
$root=Split-Path -Parent $env:INSTALL_SELF
$log=Join-Path $root 'install_new_pc.log'
$cache=Join-Path $root '_setup_cache'
$script:failures=0
$script:studioReady=$false
$script:aiReady=$false
$script:ffmpegReady=$false
Set-Location -LiteralPath $root
if (-not (Test-Path -LiteralPath $cache)) { New-Item -ItemType Directory -Path $cache -Force | Out-Null }
[IO.File]::WriteAllText($log, "FFmpeg-reuse portable install - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`r`nProject root: $root`r`n", $enc)
function Say([string]$type,[string]$message) {
    $line="[$type] $message"
    Write-Host $line
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
}
function Fail([string]$message) { $script:failures++; Say 'ERROR' $message }
function Step([string]$message) {
    Write-Host "`n=========================================================="
    Say 'STEP' $message
    Write-Host '=========================================================='
}
function Run([string]$executable,[string[]]$arguments,[string]$message,[bool]$verbose=$false) {
    Say 'RUN' $message
    $old=$ErrorActionPreference
    $ErrorActionPreference='Continue'
    try {
        $global:LASTEXITCODE=0
        & $executable @arguments 2>&1 | ForEach-Object {
            $line=[string]$_
            Add-Content -LiteralPath $log -Value $line -Encoding UTF8
            if ($verbose) { Write-Host $line }
        }
        $rc=$LASTEXITCODE
        if ($rc -ne 0) { Say 'WARN' "$message exit code $rc" }
        return ($rc -eq 0)
    } catch {
        Say 'WARN' "$message failed: $($_.Exception.Message)"
        return $false
    } finally { $ErrorActionPreference=$old }
}
function TestPython([string]$executable,[int]$minor) {
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { return $false }
    $old=$ErrorActionPreference
    $ErrorActionPreference='Continue'
    try {
        & $executable '-c' "import sys,struct;assert sys.version_info[:2]==(3,$minor);assert struct.calcsize('P')*8==64" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false } finally { $ErrorActionPreference=$old }
}
function Download([string]$url,[string]$file) {
    try {
        [Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Say 'INFO' "Downloading from $url"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $file -TimeoutSec 180
        return ((Test-Path -LiteralPath $file -PathType Leaf) -and ((Get-Item -LiteralPath $file).Length -gt 1000))
    } catch {
        Say 'WARN' "Download failed: $($_.Exception.Message)"
        return $false
    }
}
function ProjectPython([int]$minor,[string]$version,[string]$folder) {
    $target=Join-Path $root $folder
    $exe=Join-Path $target 'python.exe'
    if (TestPython $exe $minor) {
        Say 'SKIP' "Python 3.$minor already works INSIDE this project: $exe"
        return $exe
    }
    Say 'INFO' "Python 3.$minor missing or broken locally. Downloading official CPython NuGet package into this project."
    $pkg=Join-Path $cache ("python-$version.nupkg")
    $url="https://api.nuget.org/v3-flatcontainer/python/$version/python.$version.nupkg"
    if (-not (Test-Path -LiteralPath $pkg -PathType Leaf)) {
        if (-not (Download $url $pkg)) {
            if (-not (Download "https://www.nuget.org/api/v2/package/python/$version" $pkg)) {
                Fail "Could not download CPython $version NuGet. Internet connection required."
                return $null
            }
        }
    }
    $temp=Join-Path $cache ("extract-$version")
    $nupkgAsZip=Join-Path $cache ("python-$version.zip")
    try {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force -Recurse }
        Copy-Item -LiteralPath $pkg -Destination $nupkgAsZip -Force
        Expand-Archive -LiteralPath $nupkgAsZip -DestinationPath $temp -Force
        $tools=Join-Path $temp 'tools'
        if (-not (Test-Path -LiteralPath (Join-Path $tools 'python.exe') -PathType Leaf)) {
            throw 'NuGet package has no tools/python.exe'
        }
        $staging=Join-Path $root ($folder+'.installing')
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
        New-Item -ItemType Directory -Path $staging -Force | Out-Null
        Copy-Item -Path (Join-Path $tools '*') -Destination $staging -Recurse -Force
        if (-not (TestPython (Join-Path $staging 'python.exe') $minor)) {
            throw 'Extracted Python executable did not pass version/64-bit test'
        }
        if (Test-Path -LiteralPath $target) {
            $backup=Join-Path $root ($folder+'.backup-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
            Move-Item -LiteralPath $target -Destination $backup
            Say 'INFO' "Old broken runtime backed up: $backup"
        }
        Move-Item -LiteralPath $staging -Destination $target
        if (-not (TestPython $exe $minor)) { throw 'Python failed after moving into final project folder' }
        Say 'OK' "Python $version ready: $exe"
        return $exe
    } catch {
        Fail "Failed to install local Python $version : $($_.Exception.Message)"
        return $null
    }
}
function TestPip([string]$exe) {
    $prev=$ErrorActionPreference; $ErrorActionPreference='Continue'
    try {
        & $exe '-m' 'pip' '--version' 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false } finally { $ErrorActionPreference=$prev }
}
function EnsurePip([string]$exe,[int]$minor) {
    if (TestPip $exe) { Say 'SKIP' "pip already present in local Python 3.$minor"; return $true }
    $name=if ($minor -eq 7) { 'get-pip-3.7.py' } else { 'get-pip-3.11.py' }
    $bootstrap=Join-Path $cache $name
    $url=if ($minor -eq 7) { 'https://bootstrap.pypa.io/pip/3.7/get-pip.py' } else { 'https://bootstrap.pypa.io/get-pip.py' }
    if (-not (Test-Path -LiteralPath $bootstrap -PathType Leaf)) {
        if (-not (Download $url $bootstrap)) { Fail "Could not download $name"; return $false }
    }
    $a=@($bootstrap)
    if ($minor -eq 7) { $a+=@('pip==23.3.2') }
    if (-not (Run $exe $a "Bootstrap local pip for Python 3.$minor" $true)) {
        Fail "Could not bootstrap pip for Python 3.$minor"
        return $false
    }
    return (TestPip $exe)
}
function TestSource([string]$exe,[string]$code,[string]$description) {
    # A Python script in _setup_cache puts that folder into sys.path[0], NOT
    # the LaughDetectionV2 root. Prepend the root derived from this probe's
    # own location, so neither cwd, Registry nor an old project path is used.
    $probe=Join-Path $cache ('ldv2-test-'+[guid]::NewGuid().ToString('N')+'.py')
    $prefix=@'
import os as _ld_os
import sys as _ld_sys
_ld_root = _ld_os.path.dirname(_ld_os.path.dirname(_ld_os.path.abspath(__file__)))
if _ld_root not in _ld_sys.path:
    _ld_sys.path.insert(0, _ld_root)
_ld_os.chdir(_ld_root)
'@
    try {
        [IO.File]::WriteAllText($probe, ($prefix + "`n" + $code), [Text.Encoding]::ASCII)
        return (Run $exe @($probe) $description $true)
    } finally { Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue }
}
function TestYtDlp([string]$exe) {
    return (TestSource $exe @'
import yt_dlp
print('yt-dlp import OK')
'@ 'Check project-local yt-dlp')
}
function TestStudio([string]$exe) {
    return (TestSource $exe @'
import sys
assert sys.version_info[:2] == (3, 11)
import yt_dlp
import studio_server
print('Studio + yt-dlp ready')
'@ 'Validate Studio imports from this project')
}
function TestLaugh([string]$exe) {
    return (TestSource $exe @'
import sys
assert sys.version_info[:2] == (3, 7)
import pkg_resources
from pathlib import Path
reqs = [x.strip() for x in Path('requirements_laugh_py37.txt').read_text(encoding='utf-8').splitlines() if x.strip() and not x.lstrip().startswith('#')]
pkg_resources.require(reqs)
import tensorflow as tf
import keras
from audioset import vggish_embeddings
assert tf.__version__.startswith('1.15.'), tf.__version__
print('Python 3.7 / TensorFlow 1.15 / VGGish ready')
'@ 'Validate pinned AI dependencies')
}
function LocalBin([string]$filename) {
    foreach ($path in @((Join-Path $root $filename), (Join-Path $root "tools\ffmpeg\bin\$filename"))) {
        if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
    }
    return $null
}
function FFmpeg {
    # Local-first; reuse any working FFmpeg already installed on THIS Windows PC.
    # Never follow a path into a different LaughDetectionV2 project.
    $binRoot=Join-Path $root 'tools\ffmpeg\bin'
    $localFF=LocalBin 'ffmpeg.exe'
    $localFP=LocalBin 'ffprobe.exe'
    if ($localFF -and $localFP) {
        if ((Run $localFF @('-version') 'Verify existing local ffmpeg') -and (Run $localFP @('-version') 'Verify existing local ffprobe')) {
            Say 'SKIP' 'FFmpeg/ffprobe already work INSIDE this project. No download.'
            return @($localFF,$localFP)
        }
        Say 'WARN' 'Local FFmpeg files exist but are not runnable. Looking for a working installed copy.'
    }
    Say 'INFO' 'Searching for an existing FFmpeg installation. No large ZIP download will occur.'
    $candidates=New-Object 'System.Collections.Generic.List[string]'
    foreach ($command in @('ffmpeg.exe','ffmpeg')) {
        try {
            $cmd=Get-Command -Name $command -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($cmd -and $cmd.Source) { [void]$candidates.Add([string]$cmd.Source) }
        } catch { }
    }
    $searchRoots=@($env:ProgramFiles,${env:ProgramFiles(x86)},$env:ProgramW6432)
    foreach ($p in $searchRoots) {
        if (-not $p -or -not (Test-Path -LiteralPath $p -PathType Container)) { continue }
        try {
            Get-ChildItem -LiteralPath $p -Directory -Filter 'ffmpeg*' -ErrorAction SilentlyContinue | ForEach-Object {
                foreach ($trial in @((Join-Path $_.FullName 'bin\ffmpeg.exe'),(Join-Path $_.FullName 'ffmpeg.exe'))) {
                    if (Test-Path -LiteralPath $trial -PathType Leaf) { [void]$candidates.Add($trial) }
                }
            }
        } catch { }
    }
    # Common machine-wide installation directories, not project paths.
    foreach ($p in @((Join-Path $env:SystemDrive 'ffmpeg\bin\ffmpeg.exe'),(Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\ffmpeg.exe'))) {
        if ($p -and (Test-Path -LiteralPath $p -PathType Leaf)) { [void]$candidates.Add($p) }
    }
    $candidates=@($candidates | Select-Object -Unique)
    foreach ($candidate in $candidates) {
        try {
            $candidate=(Resolve-Path -LiteralPath $candidate -ErrorAction Stop).ProviderPath
            # Explicitly refuse binaries taken from other projects named LaughDetectionV2.
            if ($candidate -match '(?i)[\\/]LaughDetectionV2[\\/]' -and -not $candidate.StartsWith(($root.TrimEnd('\')+'\'),[StringComparison]::OrdinalIgnoreCase)) {
                Say 'WARN' "Skip FFmpeg path from another project: $candidate"
                continue
            }
            $sourceDir=Split-Path -Parent $candidate
            $sourceFP=Join-Path $sourceDir 'ffprobe.exe'
            if (-not (Test-Path -LiteralPath $sourceFP -PathType Leaf)) { continue }
            if (-not (Run $candidate @('-version') 'Check machine FFmpeg')) { continue }
            if (-not (Run $sourceFP @('-version') 'Check machine FFprobe')) { continue }
            New-Item -ItemType Directory -Path $binRoot -Force | Out-Null
            $dstFF=Join-Path $binRoot 'ffmpeg.exe'
            $dstFP=Join-Path $binRoot 'ffprobe.exe'
            # Back up broken project copies before replacing them; leave healthy copies alone.
            if ($localFF -and (Test-Path -LiteralPath $dstFF -PathType Leaf)) {
                Copy-Item -LiteralPath $dstFF -Destination (Join-Path $cache ('ffmpeg.broken-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.exe')) -Force
                Copy-Item -LiteralPath $candidate -Destination $dstFF -Force
            } elseif (-not (Test-Path -LiteralPath $dstFF -PathType Leaf)) {
                Copy-Item -LiteralPath $candidate -Destination $dstFF -Force
            }
            if ($localFP -and (Test-Path -LiteralPath $dstFP -PathType Leaf)) {
                Copy-Item -LiteralPath $dstFP -Destination (Join-Path $cache ('ffprobe.broken-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.exe')) -Force
                Copy-Item -LiteralPath $sourceFP -Destination $dstFP -Force
            } elseif (-not (Test-Path -LiteralPath $dstFP -PathType Leaf)) {
                Copy-Item -LiteralPath $sourceFP -Destination $dstFP -Force
            }
            # Accommodate FFmpeg builds that place companion DLLs by the executables.
            Get-ChildItem -LiteralPath $sourceDir -Filter '*.dll' -File -ErrorAction SilentlyContinue | ForEach-Object {
                $destDll=Join-Path $binRoot $_.Name
                if (-not (Test-Path -LiteralPath $destDll)) { Copy-Item -LiteralPath $_.FullName -Destination $destDll }
            }
            if ((Run $dstFF @('-version') 'Check copied local FFmpeg') -and (Run $dstFP @('-version') 'Check copied local FFprobe')) {
                Say 'OK' 'Reused existing FFmpeg and copied it into this project. No download.'
                return @($dstFF,$dstFP)
            }
            Say 'WARN' 'Machine FFmpeg worked but copied files did not. Keep the original binaries and try another installation.'
        } catch { Say 'WARN' "Could not reuse FFmpeg at $candidate : $($_.Exception.Message)" }
    }
    Fail "FFmpeg is not available. Copy ffmpeg.exe and ffprobe.exe (and any required DLLs) from a working installation to $binRoot, then rerun. No FFmpeg was downloaded."
    return @($null,$null)
}
function RelativeBin([string]$file) {
    if (-not $file) { return '' }
    $prefix=$root.TrimEnd('\')+'\'
    if (-not $file.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) {
        throw "External path not allowed in config: $file"
    }
    return $file.Substring($prefix.Length).Replace('\','/')
}
function Configure([string]$ff,[string]$fp) {
    $configFile=Join-Path $root 'config.json'
    try {
        $data=@{}
        $changed=-not (Test-Path -LiteralPath $configFile -PathType Leaf)
        if (-not $changed) {
            $raw=Get-Content -LiteralPath $configFile -Raw -Encoding UTF8
            $parsed=ConvertFrom-Json $raw
            foreach ($p in $parsed.PSObject.Properties) { $data[$p.Name]=$p.Value }
        }
        $values=@{
            detector_project_dir=''; detector_python='';
            yt_dlp='yt-dlp'; ffmpeg=(RelativeBin $ff); ffprobe=(RelativeBin $fp)
        }
        foreach ($k in $values.Keys) {
            if (-not $data.ContainsKey($k) -or [string]$data[$k] -cne [string]$values[$k]) {
                $data[$k]=$values[$k]; $changed=$true
            }
        }
        if ($changed) {
            if (Test-Path -LiteralPath $configFile -PathType Leaf) {
                $backup=Join-Path $root 'config.before_install.json'
                if (-not (Test-Path -LiteralPath $backup)) { Copy-Item -LiteralPath $configFile -Destination $backup }
            }
            [IO.File]::WriteAllText($configFile,($data|ConvertTo-Json -Depth 20)+"`r`n",(New-Object Text.UTF8Encoding($false)))
            Say 'OK' 'config.json: project-relative paths only. Other options preserved.'
        } else { Say 'SKIP' 'config.json already uses local project paths.' }
    } catch { Fail "Could not save config.json: $($_.Exception.Message)" }
}
function Launcher {
    $path=Join-Path $root 'START_STUDIO.bat'
    # Launcher text is shipped alongside this installer in V5 ZIP. For BAT-only
    # hotfixes, embedded fallback is written if the older launcher is found.
    $code=@'
@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if errorlevel 1 goto :BAD_FOLDER
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONNOUSERSITE=1"
set "PYTHONHOME="
set "PYTHONPATH="
set "PATH=%~dp0_python311\Scripts;%~dp0_python311;%~dp0tools\ffmpeg\bin;%~dp0;%PATH%"
if not exist "%~dp0_python311\python.exe" goto :MISSING
"%~dp0_python311\python.exe" "%~dp0studio_server.py"
set "RC=%errorlevel%"
echo.
if not "%RC%"=="0" echo [ERROR] Studio exited with code %RC%.
pause
exit /b %RC%
:MISSING
echo [ERROR] This folder has no _python311\python.exe.
echo Run INSTALL_NEW_PC.bat INSIDE this LaughDetectionV2 folder.
pause
exit /b 1
:BAD_FOLDER
echo [ERROR] Could not access the LaughDetectionV2 folder.
pause
exit /b 1
'@
    try {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $existing=Get-Content -LiteralPath $path -Raw
            if ($existing.Trim().Replace("`r`n","`n") -ceq $code.Trim().Replace("`r`n","`n")) {
                Say 'SKIP' 'START_STUDIO.bat is already V5 project-local.'
                return
            }
            $backup=Join-Path $root 'START_STUDIO.before_install.bat'
            if (-not (Test-Path -LiteralPath $backup)) { Copy-Item -LiteralPath $path -Destination $backup }
        }
        [IO.File]::WriteAllText($path,$code+"`r`n",[Text.Encoding]::ASCII)
        Say 'OK' 'START_STUDIO.bat updated to _python311 within project.'
    } catch { Fail "Could not update START_STUDIO.bat: $($_.Exception.Message)" }
}

Write-Host '=========================================================='
Write-Host ' LaughDetectionV2 - INSTALLER V4-STYLE - NO FFMPEG DOWNLOAD'
Write-Host " Root from BAT: $root"
Write-Host ' All runtimes are read ONLY from the current LaughDetectionV2 folder.'
Write-Host ' No system Python, Registry, parent project, or external virtual env is used.'
Write-Host " Log: $log"
Write-Host '=========================================================='
Step '0/5 Check source files inside this project'
$needed=@('studio_server.py','pipeline_manager.py','pipeline_worker.py','pipeline_common.py','subtitle_youtube.py','tai_chi_cat.py','live_inference_AI_ready.py','requirements_laugh_py37.txt','ui\index.html','audioset\vggish_pca_params.npz','Models\LSTM_SingleLayer_100Epochs.h5')
foreach ($item in $needed) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $item) -PathType Leaf)) { Fail "Missing project file: $item" }
}
if ($script:failures) { Say 'ERROR' 'Copy the entire LaughDetectionV2 project including model files.'; exit 1 }
foreach ($folder in @('jobs','done','logs','output_data','input_videos')) {
    $dir=Join-Path $root $folder
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
}
Step '1/5 Local Python 3.11 Studio and yt-dlp'
$studio=ProjectPython 11 '3.11.9' '_python311'
if ($studio) {
    if (EnsurePip $studio 11) {
        if (-not (TestYtDlp $studio)) {
            [void](Run $studio @('-m','pip','install','--disable-pip-version-check','--progress-bar','off','--no-warn-script-location','yt-dlp') 'Install yt-dlp locally' $true)
        } else { Say 'SKIP' 'yt-dlp already installed inside the project.' }
        if (TestYtDlp $studio) {
            $script:studioReady=TestStudio $studio
            if (-not $script:studioReady) { Fail 'Studio import check failed. See Python traceback above.' }
        } else { Fail 'Could not import project-local yt-dlp after installation.' }
    } else { Fail 'Python 3.11 pip unavailable.' }
}
Step '2/5 Local Python 3.7 detector and pinned AI packages'
$ai=ProjectPython 7 '3.7.9' '_python37'
if ($ai) {
    if (EnsurePip $ai 7) {
        if (-not (TestLaugh $ai)) {
            if (Run $ai @('-m','pip','install','--disable-pip-version-check','--progress-bar','off','pip==23.3.2','setuptools==65.6.3','wheel==0.41.3') 'Set pinned pip tooling locally' $true) {
                if (Run $ai @('-m','pip','install','--disable-pip-version-check','--progress-bar','off','-r','requirements_laugh_py37.txt') 'Install pinned AI dependencies into local _python37' $true) {
                    $script:aiReady=TestLaugh $ai
                }
            }
        } else { $script:aiReady=$true; Say 'SKIP' 'AI dependencies already installed locally.' }
        if (-not $script:aiReady) { Fail 'Python 3.7 model imports/pinned dependencies not ready.' }
    } else { Fail 'Python 3.7 pip unavailable.' }
}
Step '3/5 FFmpeg - reuse machine install, never download large ZIP'
$video=FFmpeg
if ($video.Count -ge 2 -and $video[0] -and $video[1]) {
    $script:ffmpegReady=(Run $video[0] @('-version') 'Validate local ffmpeg') -and (Run $video[1] @('-version') 'Validate local ffprobe')
    if (-not $script:ffmpegReady) { Fail 'FFmpeg validation failed. Copy working FFmpeg binaries under tools/ffmpeg/bin.' }
}
Step '4/5 Local config and START_STUDIO launcher'
if ($script:ffmpegReady) { Configure $video[0] $video[1] }
if ($script:studioReady -and $script:ffmpegReady) { Launcher }
else { Say 'WARN' 'Launcher unchanged until Studio and FFmpeg are ready.' }
if (Test-Path -LiteralPath (Join-Path $root 'cookies.txt')) {
    Say 'INFO' 'cookies.txt detected and left untouched. Do not share private browser cookies with employees.'
}
Step '5/5 Summary'
Write-Host " Local Python 3.11 + Studio: $script:studioReady"
Write-Host " Local Python 3.7 + AI:     $script:aiReady"
Write-Host " FFmpeg ready (project copy): $script:ffmpegReady"
Write-Host " Failures:                 $script:failures"
Say 'INFO' "Full log: $log"
if ($script:studioReady -and $script:aiReady -and $script:ffmpegReady -and $script:failures -eq 0) {
    Say 'OK' 'Everything ready inside LaughDetectionV2. Run START_STUDIO.bat.'
    exit 0
}
Say 'ERROR' 'Setup incomplete; check the first failure. FFmpeg was NOT downloaded.'
exit 1
