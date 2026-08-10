@echo off
setlocal
where py >nul 2>&1
if %errorlevel% equ 0 goto use_py
where python >nul 2>&1
if %errorlevel% equ 0 goto use_python
echo Python 3.10 or newer is required. 1>&2
exit /b 2

:use_py
py -3 "%~dp0install.py" %*
exit /b %errorlevel%

:use_python
python "%~dp0install.py" %*
exit /b %errorlevel%
