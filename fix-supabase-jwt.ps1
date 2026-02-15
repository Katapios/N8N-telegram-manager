$ErrorActionPreference = "Stop"
$envFile = ".\.env"

if (!(Test-Path $envFile)) { throw ".env not found" }

function To-Base64Url([byte[]]$bytes) {
  $b64 = [Convert]::ToBase64String($bytes)
  $b64 = $b64.TrimEnd('=').Replace('+','-').Replace('/','_')
  return $b64
}

function HmacSha256([string]$data, [string]$secret) {
  $hmac = New-Object System.Security.Cryptography.HMACSHA256
  $hmac.Key = [Text.Encoding]::UTF8.GetBytes($secret)
  $hash = $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($data))
  $hmac.Dispose()
  return $hash
}

function New-Jwt([string]$role, [string]$secret) {
  $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
  $exp = $now + 315360000
  $header = '{"alg":"HS256","typ":"JWT"}'
  $payload = "{`"role`":`"$role`",`"iss`":`"supabase`",`"aud`":`"authenticated`",`"iat`":$now,`"exp`":$exp}"

  $h = To-Base64Url ([Text.Encoding]::UTF8.GetBytes($header))
  $p = To-Base64Url ([Text.Encoding]::UTF8.GetBytes($payload))
  $sigBytes = HmacSha256 "$h.$p" $secret
  $s = To-Base64Url $sigBytes
  return "$h.$p.$s"
}

# Read .env as raw text
$content = Get-Content $envFile -Raw

# Extract JWT secret
$match = [regex]::Match($content, '(?m)^SUPABASE_JWT_SECRET="?([^"\r\n]+)"?$')
if (!$match.Success) { throw "SUPABASE_JWT_SECRET not found in .env" }
$jwtSecret = $match.Groups[1].Value
if ([string]::IsNullOrWhiteSpace($jwtSecret)) { throw "SUPABASE_JWT_SECRET is empty" }

$anon = New-Jwt "anon" $jwtSecret
$service = New-Jwt "service_role" $jwtSecret

# Replace keys
if ($content -match '(?m)^SUPABASE_ANON_KEY=') {
  $content = [regex]::Replace($content, '(?m)^SUPABASE_ANON_KEY=.*$', "SUPABASE_ANON_KEY=`"$anon`"")
} else {
  $content += "`r`nSUPABASE_ANON_KEY=`"$anon`""
}

if ($content -match '(?m)^SUPABASE_SERVICE_ROLE_KEY=') {
  $content = [regex]::Replace($content, '(?m)^SUPABASE_SERVICE_ROLE_KEY=.*$', "SUPABASE_SERVICE_ROLE_KEY=`"$service`"")
} else {
  $content += "`r`nSUPABASE_SERVICE_ROLE_KEY=`"$service`""
}

Set-Content -Path $envFile -Value $content -NoNewline -Encoding UTF8
Write-Host "Updated SUPABASE_ANON_KEY and SUPABASE_SERVICE_ROLE_KEY in .env"
