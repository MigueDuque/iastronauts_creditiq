# ─────────────────────────────────────────────────────────────────────────────
# deploy-frontend.ps1 — build CreditIQ frontend and publish to S3 + CloudFront (dev)
#
#   Usage:
#     ./deploy-frontend.ps1            # build + sync + invalidate
#     ./deploy-frontend.ps1 -SkipBuild # publish the existing dist/ (no rebuild)
#
# No secrets here (bucket + distribution id are not sensitive) — safe to commit.
# ─────────────────────────────────────────────────────────────────────────────
param(
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$Bucket         = "iastronauts-creditiq-frontend-us-east-1-dev"
$DistributionId = "E3HG9C1VM4OQNF"
$CloudFrontUrl  = "https://d3jagqz9s125b2.cloudfront.net"

# Run from the script's own directory regardless of where it was invoked.
Set-Location -Path $PSScriptRoot

function Assert-LastExit($step) {
    if ($LASTEXITCODE -ne 0) { throw "$step failed (exit $LASTEXITCODE)" }
}

if (-not $SkipBuild) {
    Write-Host "==> Building (tsc + vite, .env.production)..." -ForegroundColor Cyan
    npm run build
    Assert-LastExit "npm run build"
}

if (-not (Test-Path "dist")) {
    throw "dist/ not found — run a build first (omit -SkipBuild)."
}

Write-Host "==> Syncing dist/ -> s3://$Bucket ..." -ForegroundColor Cyan
aws s3 sync dist/ "s3://$Bucket/" --delete
Assert-LastExit "aws s3 sync"

Write-Host "==> Invalidating CloudFront cache ($DistributionId)..." -ForegroundColor Cyan
aws cloudfront create-invalidation --distribution-id $DistributionId --paths "/*" | Out-Null
Assert-LastExit "aws cloudfront create-invalidation"

Write-Host ""
Write-Host "Done. Live at $CloudFrontUrl" -ForegroundColor Green
Write-Host "(CloudFront invalidation takes ~30-60s to fully propagate.)"
