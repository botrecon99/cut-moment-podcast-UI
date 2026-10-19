@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 goto :bad_folder

title LaughDetectionV2 - Update from GitHub
color 0B
echo =========================================================
echo       LAUGH DETECTION V2 - UPDATE FROM GITHUB
echo =========================================================
echo.
echo Close Studio and detection workers before updating.
echo Working folder: "%CD%"
echo.

where git >nul 2>&1
if errorlevel 1 goto :no_git

if not exist ".git" goto :no_repo
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 goto :no_repo

git remote get-url origin >nul 2>&1
if errorlevel 1 goto :no_origin

for /f "delims=" %%B in ('git branch --show-current') do set "CURRENT_BRANCH=%%B"
if /I not "%CURRENT_BRANCH%"=="main" goto :wrong_branch

git lfs version >nul 2>&1
if errorlevel 1 goto :no_lfs

echo [1/4] Setting up Git LFS...
git lfs install --local
if errorlevel 1 goto :failed

echo.
echo [2/4] Checking locally modified tracked files...
set "HAS_STASH=0"
for /f "delims=" %%L in ('git status --porcelain --untracked-files=no') do set "HAS_STASH=1"
if "%HAS_STASH%"=="1" (
    echo Found local edits. Temporarily saving them with git stash...
    git stash push -m "LaughDetectionV2_AUTO_UPDATE_BACKUP"
    if errorlevel 1 goto :stash_failed
) else (
    echo No tracked local changes.
)

echo.
echo [3/4] Downloading latest code from origin/main...
git pull --ff-only origin main
if errorlevel 1 goto :pull_failed

echo.
echo [4/4] Downloading required Git LFS files...
git lfs pull origin main
if errorlevel 1 goto :lfs_failed

if "%HAS_STASH%"=="1" (
    echo.
    echo Restoring saved local edits...
    call :restore_stash
    if errorlevel 1 goto :restore_failed
)

echo.
echo =========================================================
echo UPDATE SUCCESSFUL! You can start the Studio now.
echo Your ignored folders (jobs, input_videos, local Python)
echo were not removed by this script.
echo =========================================================
echo.
pause
exit /b 0

:restore_stash
git stash apply --index "stash@{0}"
if errorlevel 1 exit /b 1
git stash drop "stash@{0}" >nul
if errorlevel 1 exit /b 1
exit /b 0

:pull_failed
echo.
echo ERROR: Cannot fast-forward origin/main.
echo The internet may be down, or this PC has local commits.
echo No forced reset was performed.
if "%HAS_STASH%"=="1" (
    echo Restoring saved local edits...
    call :restore_stash
    if errorlevel 1 goto :restore_failed
)
goto :failed

:lfs_failed
echo.
echo ERROR: Git LFS download failed. Code may already be updated.
echo Check internet access and Git LFS bandwidth, then run UPDATE.bat again.
if "%HAS_STASH%"=="1" (
    echo Restoring saved local edits...
    call :restore_stash
    if errorlevel 1 goto :restore_failed
)
goto :failed

:stash_failed
echo.
echo ERROR: Cannot save local edits. Close Studio and other
echo programs using files in this folder, then try again.
echo Check 'git stash list' if Git reports a partially saved stash.
goto :failed

:restore_failed
echo.
echo WARNING: Update could not automatically restore local edits.
echo Your saved copy is still in 'git stash list'.
echo DO NOT run 'git stash clear' or 'git reset --hard'.
echo Resolve conflicts or ask the maintainer for help.
goto :failed

:bad_folder
echo ERROR: Could not open the folder containing UPDATE.bat.
goto :failed

:no_git
echo ERROR: Git is not installed or is missing from PATH.
echo Install Git for Windows: https://git-scm.com/downloads/win
goto :failed

:no_repo
echo ERROR: This folder is not a Git clone (missing .git).
echo Clone the GitHub repository once and place UPDATE.bat
echo in the cloned repository root. A ZIP copy cannot git pull.
goto :failed

:no_origin
echo ERROR: Missing remote 'origin'. Please configure the GitHub repository.
goto :failed

:wrong_branch
echo ERROR: Current branch is "%CURRENT_BRANCH%" (expected main).
echo Switch to main before running UPDATE.bat.
goto :failed

:no_lfs
echo ERROR: Git LFS is not installed.
echo Install Git LFS: https://git-lfs.com/
goto :failed

:failed
echo.
echo UPDATE FAILED - review the message above. Your local files
echo have NOT been reset or forcibly deleted by this updater.
echo.
pause
exit /b 1
