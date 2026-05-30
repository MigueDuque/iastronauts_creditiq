#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy-frontend.sh — build CreditIQ frontend and publish to S3 + CloudFront (dev)
#
#   Usage:
#     ./deploy-frontend.sh              # build + sync + invalidate
#     ./deploy-frontend.sh --skip-build # publish the existing dist/ (no rebuild)
#
# No secrets here (bucket + distribution id are not sensitive) — safe to commit.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BUCKET="iastronauts-creditiq-frontend-us-east-1-dev"
DISTRIBUTION_ID="E3HG9C1VM4OQNF"
CLOUDFRONT_URL="https://d3jagqz9s125b2.cloudfront.net"

# Run from the script's own directory regardless of where it was invoked.
cd "$(dirname "$0")"

SKIP_BUILD=0
[[ "${1:-}" == "--skip-build" ]] && SKIP_BUILD=1

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo "==> Building (tsc + vite, .env.production)..."
  npm run build
fi

[[ -d dist ]] || { echo "dist/ not found — run a build first (omit --skip-build)."; exit 1; }

echo "==> Syncing dist/ -> s3://$BUCKET ..."
aws s3 sync dist/ "s3://$BUCKET/" --delete

echo "==> Invalidating CloudFront cache ($DISTRIBUTION_ID)..."
aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths "/*" >/dev/null

echo ""
echo "Done. Live at $CLOUDFRONT_URL"
echo "(CloudFront invalidation takes ~30-60s to fully propagate.)"
