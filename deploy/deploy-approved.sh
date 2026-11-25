#!/usr/bin/env bash
# Apply only owner-approved changes merged into the production branch.
set -Eeuo pipefail
umask 077
REPO=/opt/coinbase-bots
STATE_DIR=/var/lib/coinbase-approved-deploy
LAST="$STATE_DIR/deployed_sha"
SERVICE=coinbase-bots
mkdir -p "$STATE_DIR"
exec 9>"$STATE_DIR/lock"
flock -n 9 || exit 0
cd "$REPO"
git fetch --quiet --no-tags origin refs/heads/production:refs/remotes/origin/production
TARGET=$(git rev-parse 'refs/remotes/origin/production^{commit}')
if [[ ! -s "$LAST" ]]; then
  echo "$TARGET" > "$LAST"
  echo "Initialized approval baseline at $TARGET; no files changed."
  exit 0
fi
PREVIOUS=$(cat "$LAST")
[[ "$PREVIOUS" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid deployment state'; exit 1; }
[[ "$PREVIOUS" != "$TARGET" ]] || exit 0
git merge-base --is-ancestor "$PREVIOUS" "$TARGET" || { echo 'Production branch rewound; refusing'; exit 1; }
mapfile -t CHANGED < <(git diff --name-only --diff-filter=ACMR "$PREVIOUS" "$TARGET")
mapfile -t DELETED < <(git diff --name-only --diff-filter=D "$PREVIOUS" "$TARGET")
(("${#DELETED[@]}" == 0)) || { echo 'Deleted files require manual review'; exit 1; }
ALLOWED='^(competition|market_base|new_listing_sniper|all_on_black|ws_monitor|live_entry|dashboard_plus|export_report)\.py$'
DEPLOY=()
for p in "${CHANGED[@]}"; do
  if [[ "$p" =~ $ALLOWED ]]; then DEPLOY+=("$p"); fi
done
if (("${#DEPLOY[@]}" == 0)); then
  echo "$TARGET" > "$LAST"
  echo 'No deployable application code changed.'
  exit 0
fi
STAGE=$(mktemp -d "$STATE_DIR/stage.XXXXXXXX")
BACKUP=$(mktemp -d "$STATE_DIR/backup.XXXXXXXX")
trap 'rm -rf "$STAGE"' EXIT
for name in competition.py market_base.py new_listing_sniper.py all_on_black.py ws_monitor.py live_entry.py dashboard_plus.py export_report.py; do
  [[ ! -f "$REPO/$name" ]] || cp -p "$REPO/$name" "$STAGE/$name"
done
cp "$REPO/config.json" "$STAGE/config.json"
for name in "${DEPLOY[@]}"; do
  git show "$TARGET:$name" > "$STAGE/$name"
  [[ -f "$REPO/$name" ]] && cp -p "$REPO/$name" "$BACKUP/$name"
done
"$REPO/.venv/bin/python" -m compileall -q "$STAGE"
for test in test_live_entries.py test_shared_websocket.py; do
  if git cat-file -e "$TARGET:$test" 2>/dev/null; then
    git show "$TARGET:$test" > "$STAGE/$test"
    (cd "$STAGE" && PYTHONPATH="$STAGE" "$REPO/.venv/bin/python" "$test")
  fi
done
systemctl stop "$SERVICE"
failed=0
for name in "${DEPLOY[@]}"; do cp -p "$STAGE/$name" "$REPO/$name" || failed=1; done
if ((failed == 0)); then
  systemctl start "$SERVICE" || failed=1
  sleep 5
  systemctl is-active --quiet "$SERVICE" || failed=1
fi
if ((failed != 0)); then
  echo 'DEPLOYMENT FAILED — restoring previous application code' >&2
  systemctl stop "$SERVICE" || true
  for name in "${DEPLOY[@]}"; do
    if [[ -f "$BACKUP/$name" ]]; then cp -p "$BACKUP/$name" "$REPO/$name"; else rm -f "$REPO/$name"; fi
  done
  systemctl start "$SERVICE" || true
  exit 1
fi
printf '%s\n' "$TARGET" > "$LAST"
echo "DEPLOYED approved version $TARGET (${DEPLOY[*]})"
