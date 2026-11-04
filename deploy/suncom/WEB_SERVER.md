# Suncom: the web server (API + dashboard)

`suncom-nudgelab-web`, 100.59.145.97 (GreenTech account, us-east-1, Ubuntu 26.04, role `suncom-nudgelab-api`).
It serves the API at `https://nudgelabapi.mysuncomportal.com` and the dashboard at
`https://nudgelab.mysuncomportal.com`. Prime runs the same code on two servers (deploy/README.md); here both
are on one. Sign in from the laptop's Git Bash: `ssh -i /c/AWS/GreenTech/suncom-nudgelab-key.pem ubuntu@100.59.145.97`.

Until the Suncom changes are merged into `main`, the API runs the branch `suncom-anthropic`.

## 0. DNS (you)

Three A records in mysuncomportal.com's DNS:

| Name | Points to |
|---|---|
| `nudgelabapi.mysuncomportal.com` | 100.59.145.97 (this server) |
| `nudgelab.mysuncomportal.com` | 100.59.145.97 (this server) |
| `nudgelab-test.mysuncomportal.com` | 34.239.75.17 (suncom-nudgelab-main: the tester page) |

Check from the laptop: `nslookup nudgelabapi.mysuncomportal.com` gives 100.59.145.97.

## 1. Packages

```bash
sudo apt update && sudo apt install -y nginx certbot python3-certbot-nginx mysql-client git nodejs npm
node -v                                   # 22 or newer (the dashboard needs it)
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.bashrc
uv python install 3.12                    # Ubuntu 26.04 ships 3.14; the API's lock file targets 3.12
python3.12 -V
```

If `node -v` is older than 22: `curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs`.

## 2. GitHub deploy keys (read-only), one per repo

```bash
for r in nudgelabapi nudgelabdashboard; do
  ssh-keygen -t ed25519 -f ~/.ssh/${r}_deploy -N "" -C "suncom-nudgelab-web $r"
  printf 'Host github-%s\n    HostName github.com\n    User git\n    IdentityFile ~/.ssh/%s_deploy\n    IdentitiesOnly yes\n\n' $r $r >> ~/.ssh/config
  cat ~/.ssh/${r}_deploy.pub
done
```

Add each printed `.pub` line on GitHub → that repo → Settings → Deploy keys → Add (read-only). Check:
`ssh -T github-nudgelabapi` and `ssh -T github-nudgelabdashboard` both say "successfully authenticated".

## 3. Certificates

DNS must point here first (step 0).

```bash
sudo certbot certonly --nginx -d nudgelabapi.mysuncomportal.com
sudo certbot certonly --nginx -d nudgelab.mysuncomportal.com
```

It asks for an email for expiry notices the first time. One certificate per name, at the paths the two nginx
sites expect. Renewal is automatic (`systemctl list-timers | grep certbot`).

## 4. The API: code and settings

```bash
sudo mkdir -p /srv/nudgelabapi /var/lib/nudgelabapi/exports && sudo chown -R ubuntu:ubuntu /srv/nudgelabapi /var/lib/nudgelabapi
git clone github-nudgelabapi:itsassbs2026/nudgelabapi.git /srv/nudgelabapi
cd /srv/nudgelabapi && git checkout suncom-anthropic
python3.12 -m venv venv && venv/bin/pip install -q -r requirements.lock

cp deploy/suncom/env.suncom.example .env && chmod 600 .env
nano .env                                  # every FILL; passwords from 01-logins.sql, keys from your password manager
bash deploy/preflight-check.sh
```

The preflight should pass `database` (it connects as `nudgelab_api`, over TLS) and the AWS lines; the API tables,
migrations, services and nginx are still WARN/FAIL at this point. Fix any other FAIL first.

## 5. Migrations, then the grants and seeds (you, on RDS)

```bash
venv/bin/alembic current                  # nothing: an empty nudgeai
venv/bin/alembic upgrade head             # creates every table, 0001 to 0024
venv/bin/alembic current                  # 0024_app_handoffs (head)
```

Then as the RDS master: `deploy/suncom/03-grants.sql`, then `deploy/suncom/04-seed-voices-setups.sql`.

## 6. The first Admin, the services, nginx

```bash
venv/bin/python -m scripts.bootstrap_admin     # you, with the temporary password from .env
sed -i '/^BOOTSTRAP_ADMIN_PASSWORD=/d' .env

sudo cp deploy/nudgelabapi.service deploy/nudgelabapi-worker.service \
        deploy/nudgelabapi-reference-sync.service deploy/nudgelabapi-reference-sync.timer \
        deploy/nudgelabapi-daily-summary.service deploy/nudgelabapi-daily-summary.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nudgelabapi nudgelabapi-worker

sudo cp deploy/suncom/nginx-nudgelabapi.suncom.conf /etc/nginx/sites-available/nudgelabapi
sudo ln -sf /etc/nginx/sites-available/nudgelabapi /etc/nginx/sites-enabled/nudgelabapi
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

curl -s https://nudgelabapi.mysuncomportal.com/api/v1/health    # {"status":"ok","db":"ok",...}
bash deploy/preflight-check.sh                                   # no FAIL
```

## 7. Employees and stores from Suncom's Portal

```bash
venv/bin/python scripts/sync_reference_tables.py --dry-run   # rows per view on suncomportal; no writes
venv/bin/python scripts/sync_reference_tables.py             # all four SUCCESS
sudo systemctl enable --now nudgelabapi-reference-sync.timer
```

A view that's missing or has other columns than Prime's shows here: send me the output.
Leave `nudgelabapi-daily-summary.timer` off until email is set up.

## 8. The dashboard

```bash
sudo mkdir -p /srv/nudgelabdashboard /var/www/nudgelabdashboard && sudo chown ubuntu:ubuntu /srv/nudgelabdashboard /var/www/nudgelabdashboard
git clone github-nudgelabdashboard:itsassbs2026/nudgelabdashboard.git /srv/nudgelabdashboard
cd /srv/nudgelabdashboard
printf 'VITE_API_BASE_URL=https://nudgelabapi.mysuncomportal.com/api/v1\nVITE_APP_ENV=prod\nVITE_TESTER_URL=https://nudgelab-test.mysuncomportal.com\n' > .env
bash deploy/deploy.sh                      # builds and publishes the first release

sudo cp deploy/nginx-nudgelab.suncom.conf /etc/nginx/sites-available/nudgelab
sudo ln -sf /etc/nginx/sites-available/nudgelab /etc/nginx/sites-enabled/nudgelab
sudo nginx -t && sudo systemctl reload nginx
bash deploy/preflight-check.sh             # no FAIL
```

## 9. Check

Open `https://nudgelab.mysuncomportal.com`, sign in as the first Admin with the temporary password, set a new
one. Then Admin > Settings: the company's name and wording (company.*: "Suncom Mobile", app name, Portal name).
Tell me and I check the rest from here (health, logins, sync counts) before the agent servers.
