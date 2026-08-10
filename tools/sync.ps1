<#
.SYNOPSIS
  One command to push your work before switching to another PC.

.DESCRIPTION
  Stages everything, commits (with a message you give or a timestamped
  default), and pushes to origin. Pairs with the SessionStart hook in
  .claude/settings.json, which auto-pulls on the OTHER machine next time
  Claude Code opens there - so run this before you close the lid, and the
  other PC picks it up with zero manual steps.

  Safe to run with nothing to commit - it just says so and stops.

.EXAMPLE
  .\tools\sync.ps1
  .\tools\sync.ps1 "finished the diagnostics rules"
#>
param(
    [Parameter(Position = 0)]
    [string]$Message
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$git = (Get-Command git -ErrorAction SilentlyContinue).Source
if (-not $git) { $git = "C:\Program Files\Git\cmd\git.exe" }
if (-not (Test-Path $git)) {
    Write-Error "Git was not found. Install it and try again."
    exit 1
}

$status = & $git status --porcelain
if (-not $status) {
    Write-Output "Nothing to sync - working tree is already clean."
    $ahead = & $git rev-list --count '@{u}..HEAD' 2>$null
    if ($ahead -and [int]$ahead -gt 0) {
        Write-Output "But you have $ahead unpushed commit(s) - pushing now..."
        & $git push
    }
    exit 0
}

Write-Output "Changes to sync:"
& $git status --short

if (-not $Message) {
    $Message = "Sync: $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
}

& $git add -A
& $git commit -m $Message
& $git push

Write-Output ""
Write-Output "Pushed. The other PC will pick this up automatically next time"
Write-Output "Claude Code starts there (or run 'git pull' by hand)."
