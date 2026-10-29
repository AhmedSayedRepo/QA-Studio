<#
  push.ps1 — one-shot release helper for QA Studio.

  Does the whole ritual:
    1. (optional) stages + commits any changes with your message
    2. pushes the branch
    3. reads VERSION, creates tag  v<version>  on the current commit
    4. pushes the tag

  Guards:
    - refuses to tag if  v<version>  already exists (means you forgot to bump
      VERSION) — both locally and on the remote
    - cleans BOM/whitespace out of VERSION so the tag name is always clean

  Usage:
    .\push.ps1                       # commit (if needed) + push + tag + push tag
    .\push.ps1 "Release notes here"  # use that commit message
    .\push.ps1 -NoCommit             # skip committing; just push + tag HEAD
#>

[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [string]$Message = "",
  [switch]$NoCommit,
  [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# --- must be inside a git repo ---
git rev-parse --is-inside-work-tree *> $null
if ($LASTEXITCODE -ne 0) { Fail "Not inside a git repository." }

# --- VERSION must exist and be readable ---
if (-not (Test-Path "VERSION")) { Fail "No VERSION file in the repo root." }
$raw = Get-Content -Raw "VERSION"
$ver = ($raw -replace '[^\d.]', '').Trim('.')
if ([string]::IsNullOrWhiteSpace($ver)) { Fail "VERSION file is empty or invalid (got '$raw')." }
$tag = "v$ver"
Write-Host "Version: $ver  ->  tag $tag" -ForegroundColor Cyan

# --- guard: must be ON the branch we push ---
# Otherwise the commit below lands on the current branch while `git push origin
# $Branch` pushes the OTHER (stale) branch, and the tag is cut from a commit
# that never reaches $Branch: a release without its changes.
$current = (git rev-parse --abbrev-ref HEAD).Trim()
if ($current -ne $Branch) {
  Fail "You are on branch '$current' but releases push '$Branch'. Run: git switch $Branch  (bring your changes with you), then re-run with the same version."
}

# --- commit any pending changes (unless -NoCommit) ---
if (-not $NoCommit) {
  $dirty = git status --porcelain
  if ($dirty) {
    # `git add -A` also sweeps in NEW files (leftovers from other branches,
    # scratch files). List them and require an explicit yes.
    $untracked = @(git ls-files --others --exclude-standard)
    if ($untracked.Count -gt 0) {
      Write-Host "These NEW (untracked) files will be included in the release commit:" -ForegroundColor Yellow
      $untracked | ForEach-Object { Write-Host "  + $_" }
      $ans = Read-Host "Include them? (y/N)"
      if ($ans -notmatch '^(y|yes)$') {
        Fail "Aborted before committing. Delete the unwanted files (or list them in .git\info\exclude), then re-run with the same version."
      }
    }
    if ([string]::IsNullOrWhiteSpace($Message)) { $Message = "Release $tag" }
    Write-Host "Committing changes: $Message" -ForegroundColor Yellow
    git add -A
    git commit -m $Message
    if ($LASTEXITCODE -ne 0) { Fail "git commit failed." }
  } else {
    Write-Host "No pending changes to commit." -ForegroundColor DarkGray
  }
}

# --- push the branch ---
Write-Host "Pushing $Branch..." -ForegroundColor Yellow
git push origin $Branch
if ($LASTEXITCODE -ne 0) { Fail "git push failed." }

# --- guard: does the tag already exist locally or on the remote? ---
$localTag = git tag --list $tag
if ($localTag) { Fail "Tag $tag already exists locally. Bump VERSION before releasing." }

$remoteTag = git ls-remote --tags origin "refs/tags/$tag"
if ($remoteTag) { Fail "Tag $tag already exists on the remote. Bump VERSION before releasing." }

# --- create + push the tag on the current commit ---
Write-Host "Tagging $tag..." -ForegroundColor Yellow
git tag -a $tag -m "Release $tag"
if ($LASTEXITCODE -ne 0) { Fail "git tag failed." }

git push origin $tag
if ($LASTEXITCODE -ne 0) { Fail "Pushing tag failed." }

Write-Host "Done. Released $tag" -ForegroundColor Green
