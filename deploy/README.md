# Approved paper bot deployments

Normal edits to `main` do **not** deploy. Only a reviewed pull request **merged into `production`** authorizes a release.

## One-time DigitalOcean setup

Run these commands through the existing DigitalOcean Web Console:

```bash
cd /opt/coinbase-bots
git fetch origin main production
git show origin/main:deploy/deploy-approved.sh > /usr/local/bin/coinbase-approved-deploy
chmod 700 /usr/local/bin/coinbase-approved-deploy
/usr/local/bin/coinbase-approved-deploy
```

The initial call only records the currently approved version. It doesn't overwrite the existing bots.

Then install the timer:

```bash
cat > /etc/systemd/system/coinbase-approved-deploy.service <<'UNIT'
[Unit]
Description=Deploy reviewed Coinbase paper trading bot code
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/coinbase-approved-deploy
UNIT

cat > /etc/systemd/system/coinbase-approved-deploy.timer <<'TIMER'
[Unit]
Description=Check for approved bot updates every two minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
Unit=coinbase-approved-deploy.service

[Install]
WantedBy=timers.target
TIMER

systemctl daemon-reload
systemctl enable --now coinbase-approved-deploy.timer
systemctl list-timers coinbase-approved-deploy.timer --no-pager
```

## Workflow

1. Propose a change on a feature branch and review its pull request into `production`.
2. The owner **merges** the pull request to approve the deployment. A pull request that is only reviewed but not merged does not deploy.
3. Within approximately two minutes the server stages changed allowlisted Python modules, compiles them, runs offline regression tests, saves prior source files, updates the code and restarts the paper trading service.
4. Failed startup restores previous source files. The trading history and local `config.json` are never overwritten by the deployment script.
5. View deployment logs: `journalctl -u coinbase-approved-deploy.service -n 80 --no-pager`.

Do not merge unreviewed code. The `production` branch grants the ability to execute code on the server; restrict who can merge and use GitHub branch protection when available. This script does not update itself. It does not deploy `.env`, `data/`, or `config.json`, and its initial setup does not overwrite locally customized strategies.

Offline tests and a running process are basic checks, **not** proof of strategy profitability or full runtime correctness.
