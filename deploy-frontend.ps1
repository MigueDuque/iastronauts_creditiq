# ─────────────────────────────────────────────────────────────────────────────
# CreditIQ — Frontend Deploy Script
# Usage: .\deploy-frontend.ps1
# ─────────────────────────────────────────────────────────────────────────────
$BUCKET  = "iastronauts-creditiq-frontend-us-east-1-dev"
$DIST_ID = "E22MO7V3YSN09K"
$REGION  = "us-east-1"

Set-Location "$PSScriptRoot\iastronauts_creditiq_front"

Write-Host "Building..." -ForegroundColor Cyan
npm run build
if ($LASTEXITCODE -ne 0) { Write-Host "Build failed" -ForegroundColor Red; exit 1 }

Write-Host "Syncing to S3 (preserving /icons)..." -ForegroundColor Cyan
# --exclude "icons/*" ensures the static icon assets uploaded separately are never deleted
aws s3 sync dist/ "s3://$BUCKET/" --delete --exclude "icons/*" --region $REGION

Write-Host "Invalidating CloudFront cache..." -ForegroundColor Cyan
aws cloudfront create-invalidation --distribution-id $DIST_ID --paths "/*" --region $REGION --query "Invalidation.Id" --output text

Write-Host "Done. Live at: https://d1e71xi46mwhhn.cloudfront.net" -ForegroundColor Green
