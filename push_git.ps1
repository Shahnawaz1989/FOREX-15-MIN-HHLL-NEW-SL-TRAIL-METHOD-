Set-Location $PSScriptRoot

Write-Host "[1/4] Checking git repo..."
git rev-parse --is-inside-work-tree *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "This folder is not a git repository."
    pause
    exit 1
}

Write-Host "[2/4] Adding changes..."
git add -A

Write-Host "[3/4] Committing changes..."
$changes = git status --porcelain
if ($changes) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    git commit -m "quick update $ts"
} else {
    Write-Host "No changes to commit."
}

Write-Host "[4/4] Pulling and pushing..."
git pull --rebase
if ($LASTEXITCODE -ne 0) {
    Write-Host "Pull failed. Resolve conflicts, then run again."
    pause
    exit 1
}

git push
if ($LASTEXITCODE -ne 0) {
    Write-Host "Push failed."
    pause
    exit 1
}

Write-Host "Done."
pause