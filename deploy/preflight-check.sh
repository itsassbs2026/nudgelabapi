#!/usr/bin/env bash
# Read-only checks on the API server before going live, and after every deploy (SPEC §14.1).
# Changes nothing. Run as ubuntu:   bash /srv/nudgelabapi/deploy/preflight-check.sh
#
# Server checks here (DNS, certificate, Python, port, nginx, systemd, export folder), then the app checks in
# scripts/preflight.py (settings, database logins and grants, migrations, S3, LiveKit, Graph).

set -u
APP_DIR=/srv/nudgelabapi
API_HOST=nudgelabapi.myprimeportal.com
CERT=/etc/ssl/myprimeportal/star.myprimeportal.com.fullchain.crt
fails=0

pass() { printf 'PASS  %s\n' "$1"; }
warn() { printf 'WARN  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; fails=$((fails + 1)); }

echo "== server"
if command -v dig >/dev/null 2>&1; then
  resolved=$(dig +short "$API_HOST" | tail -n1)
  [ -n "$resolved" ] && pass "DNS $API_HOST -> $resolved" || fail "DNS: $API_HOST doesn't resolve yet (Route 53)"
else
  getent hosts "$API_HOST" >/dev/null && pass "DNS $API_HOST resolves" || fail "DNS: $API_HOST doesn't resolve yet"
fi

if [ -r "$CERT" ]; then
  # Browsers get the load balancer's own (Amazon) certificate; this file is used between the load balancer and
  # nginx, like pingitapi's. An expired one doesn't stop the site, but whoever runs these servers should renew it.
  expiry=$(openssl x509 -enddate -noout -in "$CERT" 2>/dev/null | cut -d= -f2)
  if ! openssl x509 -checkend 0 -noout -in "$CERT" >/dev/null 2>&1; then
    warn "server certificate EXPIRED $expiry (used between the load balancer and nginx; browsers get the load balancer's)"
  elif openssl x509 -checkend $((30 * 86400)) -noout -in "$CERT" >/dev/null 2>&1; then
    pass "certificate valid until $expiry"
  else
    warn "certificate expires within 30 days ($expiry)"
  fi
  openssl x509 -noout -text -in "$CERT" 2>/dev/null | grep -q '\*\.myprimeportal\.com' \
    && pass "certificate covers *.myprimeportal.com" || fail "certificate doesn't cover *.myprimeportal.com"
else
  fail "certificate not readable at $CERT (check pingitapi's nginx site for the real path)"
fi

if command -v python3.12 >/dev/null 2>&1; then pass "python3.12 $(python3.12 -V 2>&1 | cut -d' ' -f2)"; else fail "python3.12 not installed (the lock file targets 3.12)"; fi

if ss -tlnp 2>/dev/null | grep -q ':8002 '; then
  if systemctl is-active --quiet nudgelabapi; then pass "port 8002 in use by nudgelabapi"; else fail "port 8002 is taken by something else"; fi
else
  systemctl is-active --quiet nudgelabapi && fail "nudgelabapi is active but not listening on 8002" || pass "port 8002 free"
fi

if sudo -n nginx -t >/dev/null 2>&1; then pass "nginx config valid"; else warn "nginx -t failed or needs sudo (run: sudo nginx -t)"; fi
[ -L /etc/nginx/sites-enabled/nudgelabapi ] && pass "nginx site enabled" || warn "nginx site not enabled yet"

for unit in nudgelabapi nudgelabapi-worker; do
  if systemctl list-unit-files "$unit.service" >/dev/null 2>&1 && systemctl list-unit-files | grep -q "^$unit.service"; then
    systemctl is-active --quiet "$unit" && pass "$unit running" || warn "$unit installed but not running"
  else
    warn "$unit not installed yet"
  fi
done

[ -d "$APP_DIR/.git" ] && pass "repo at $APP_DIR ($(git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null))" || fail "no git clone at $APP_DIR"
[ -x "$APP_DIR/venv/bin/python" ] && pass "venv present" || fail "no venv at $APP_DIR/venv"

echo
echo "== app"
if [ -x "$APP_DIR/venv/bin/python" ]; then
  (cd "$APP_DIR" && venv/bin/python scripts/preflight.py) || fails=$((fails + 1))
fi

echo
if [ "$fails" -eq 0 ]; then echo "Preflight: no failures."; else echo "Preflight: $fails check(s) failed."; fi
exit $((fails > 0))
