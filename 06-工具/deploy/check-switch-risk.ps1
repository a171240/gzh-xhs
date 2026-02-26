[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$TargetBranch,
  [string]$CurrentBranch = "",
  [switch]$Force
)

$ErrorActionPreference = "Stop"

function Invoke-Git([string[]]$ArgsList) {
  $previousPref = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  $output = & git @ArgsList 2>&1
  $exitCode = $LASTEXITCODE
  $ErrorActionPreference = $previousPref
  if ($exitCode -ne 0) {
    throw "git $($ArgsList -join ' ') failed: $output"
  }
  return $output
}

try {
  $repoRoot = (Invoke-Git @("rev-parse", "--show-toplevel")).Trim()
  if ([string]::IsNullOrWhiteSpace($CurrentBranch)) {
    $CurrentBranch = (Invoke-Git @("branch", "--show-current")).Trim()
  }
  if ([string]::IsNullOrWhiteSpace($CurrentBranch)) {
    throw "Cannot detect current branch."
  }

  $targetLocalRef = "refs/heads/$TargetBranch"
  $targetRemoteRef = "refs/remotes/origin/$TargetBranch"
  $targetRef = ""

  & git show-ref --verify --quiet $targetLocalRef
  if ($LASTEXITCODE -eq 0) {
    $targetRef = $TargetBranch
  } else {
    & git show-ref --verify --quiet $targetRemoteRef
    if ($LASTEXITCODE -eq 0) {
      $targetRef = "origin/$TargetBranch"
    } else {
      throw "Target branch '$TargetBranch' not found locally or at origin."
    }
  }

  $raw = Invoke-Git @("diff", "--name-status", "--no-renames", "$CurrentBranch..$targetRef")
  $lines = @($raw -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
  $deleted = @($lines | Where-Object { $_ -match "^D\s+" })

  Write-Host "Repository: $repoRoot"
  Write-Host "Current:    $CurrentBranch"
  Write-Host "Target:     $targetRef"
  Write-Host "Changes:    $($lines.Count)"
  Write-Host "Deleted:    $($deleted.Count)"

  if ($deleted.Count -gt 0) {
    Write-Host ""
    Write-Host "Deleted entries when switching to '$targetRef':"
    $deleted | Select-Object -First 80 | ForEach-Object { Write-Host "  $_" }
    if ($deleted.Count -gt 80) {
      Write-Host "  ... (truncated, total $($deleted.Count))"
    }
    Write-Host ""
    if (-not $Force) {
      Write-Host "Blocked. Re-run with -Force only if you intentionally accept these deletions."
      exit 2
    }
  }

  Write-Host "Safe check passed."
  exit 0
} catch {
  Write-Error $_
  exit 1
}
