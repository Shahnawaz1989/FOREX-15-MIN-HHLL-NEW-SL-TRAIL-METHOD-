@echo off
cd /d "%~dp0"

git add -A
git commit -m "quick update"
git pull --rebase
git push

pause