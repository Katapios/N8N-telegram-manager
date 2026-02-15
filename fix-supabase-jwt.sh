#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=".env"
[ -f "$ENV_FILE" ] || { echo "No .env found"; exit 1; }

JWT_SECRET="$(grep '^SUPABASE_JWT_SECRET=' "$ENV_FILE" | sed -E 's/^SUPABASE_JWT_SECRET="?(.*)"?$/\1/')"
[ -n "$JWT_SECRET" ] || { echo "SUPABASE_JWT_SECRET is empty"; exit 1; }

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

mkjwt() {
  local role="$1"
  local now exp header payload h p s
  now="$(date +%s)"
  exp="$((now + 315360000))" # ~10 years
  header='{"alg":"HS256","typ":"JWT"}'
  payload="$(printf '{"role":"%s","iss":"supabase","aud":"authenticated","iat":%s,"exp":%s}' "$role" "$now" "$exp")"
  h="$(printf '%s' "$header" | b64url)"
  p="$(printf '%s' "$payload" | b64url)"
  s="$(printf '%s' "$h.$p" | openssl dgst -sha256 -hmac "$JWT_SECRET" -binary | b64url)"
  printf '%s.%s.%s' "$h" "$p" "$s"
}

ANON_KEY="$(mkjwt anon)"
SERVICE_KEY="$(mkjwt service_role)"

# macOS/BSD sed compatible in-place replace
sed -i.bak -E "s|^SUPABASE_ANON_KEY=.*$|SUPABASE_ANON_KEY=\"$ANON_KEY\"|g" "$ENV_FILE"
sed -i.bak -E "s|^SUPABASE_SERVICE_ROLE_KEY=.*$|SUPABASE_SERVICE_ROLE_KEY=\"$SERVICE_KEY\"|g" "$ENV_FILE"
rm -f "${ENV_FILE}.bak"

echo "Updated SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY in $ENV_FILE"
echo "Next: docker-compose up -d --force-recreate n8n supabase-kong supabase-rest supabase-storage"
