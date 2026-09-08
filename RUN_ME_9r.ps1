# RUN_ME_9r.ps1 — session 9r owner actions, 2026-09-08
#
# Two jobs: push the code, then pull down the run artifacts that every
# remaining piece of work is blocked on.
#
# Run it from PowerShell in the repo folder:
#     cd "C:\Users\CafeBazaar\Desktop\Ads\PMM\News Curator\News Curator"
#     .\RUN_ME_9r.ps1
#
# If PowerShell refuses to run it:
#     Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#
# Nothing here is destructive. Step 2 is skipped automatically if the GitHub
# CLI is not installed, and prints the browser fallback instead.

$ErrorActionPreference = "Stop"
Set-Location "C:\Users\CafeBazaar\Desktop\Ads\PMM\News Curator\News Curator"

# ---------------------------------------------------------------------------
# STEP 1 — review, commit, push session 9r
# ---------------------------------------------------------------------------
Write-Host "`n=== Changes in 9r ===" -ForegroundColor Cyan
git status --short

Write-Host "`nPress Enter to commit and push, or Ctrl+C to stop and review first." -ForegroundColor Yellow
Read-Host

git add -A
git commit -m "session 9r: same-run dedup uses the HIGH band + non-transitivity fixed; flash watchdog; CLAUDE.md 814->528 (suite 769)"
git push origin main

Write-Host "`nPushed." -ForegroundColor Green

# ---------------------------------------------------------------------------
# STEP 2 — download the run artifacts (THE BLOCKER)
# ---------------------------------------------------------------------------
# No chosen.csv / run.csv / summaries.csv / read.csv / flash_*.csv exists in
# this folder. Threshold tuning, delivered-event verification and flash tuning
# are all blocked until they do.

$dest = Join-Path (Get-Location) "artifacts"
New-Item -ItemType Directory -Force -Path $dest | Out-Null

if (Get-Command gh -ErrorAction SilentlyContinue) {
    Write-Host "`n=== Downloading the last 20 workflow runs' artifacts ===" -ForegroundColor Cyan

    # Pipeline run reports (read / chosen / summaries / run CSVs)
    gh run list --workflow pipeline.yml --limit 20 --json databaseId `
        --jq '.[].databaseId' | ForEach-Object {
            Write-Host "  pipeline run $_"
            gh run download $_ --dir (Join-Path $dest "pipeline_$_") 2>$null
        }

    # Flash monitor reports
    gh run list --workflow flash-alert.yml --limit 20 --json databaseId `
        --jq '.[].databaseId' | ForEach-Object {
            Write-Host "  flash run $_"
            gh run download $_ --dir (Join-Path $dest "flash_$_") 2>$null
        }

    Write-Host "`nDone. CSVs are under .\artifacts\" -ForegroundColor Green
    Get-ChildItem -Path $dest -Recurse -Filter *.csv | Select-Object -First 40 FullName
}
else {
    Write-Host "`nGitHub CLI (gh) is not installed — skipping the automatic download." -ForegroundColor Yellow
    Write-Host "Either install it once:"      -ForegroundColor Yellow
    Write-Host "    winget install --id GitHub.cli"
    Write-Host "    gh auth login"
    Write-Host "  then re-run this script."   -ForegroundColor Yellow
    Write-Host "`nOr do it by hand in the browser:" -ForegroundColor Yellow
    Write-Host "    https://github.com/mmousavi93-bit/news-curator/actions"
    Write-Host "    Open the last ~6 'pipeline' runs and the last ~10 'flash-alert' runs,"
    Write-Host "    download the artifact zip at the bottom of each, and extract them all"
    Write-Host "    into:  $dest"
}

# ---------------------------------------------------------------------------
# STEP 3 — sanity checks after the next scheduled run
# ---------------------------------------------------------------------------
Write-Host @"

=== After the next pipeline run (top of the next 3-hour slot) ===

1. The digest should look EXACTLY as before. The flash watchdog line
   («پایش هشدار فوری ...») appears only when the flash monitor has been
   silent for more than 3 hours. Seeing it means the monitor is actually
   down — open the Actions tab.
2. In the run log, the 'Flash-state age' step prints one line, e.g.
   'flash-state age: 12'. If it prints 'missing', the flash-state branch
   could not be fetched — that is worth telling me about.
3. Delivered-event count is the number that matters for 9q/9r:
   ~2-4 on a quiet run, ~14-20 across 2 messages on an escalation run.

"@ -ForegroundColor Cyan
