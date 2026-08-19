#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
service_label="com.codex.payer-payee.prediction-llm.8765"
service_domain="gui/$(id -u)"
service_target="$service_domain/$service_label"
plist_file="$project_dir/com.codex.payer-payee.prediction-llm.8765.plist"

if ! nc -z 127.0.0.1 27018 2>/dev/null; then
  "$project_dir/../payer_payee_mongodb/start_local_mongodb.sh"
fi

if launchctl print "$service_target" >/dev/null 2>&1; then
  launchctl kickstart -k "$service_target"
else
  launchctl bootstrap "$service_domain" "$plist_file"
fi

attempt=0
while [ "$attempt" -lt 300 ]; do
  if nc -z 127.0.0.1 8765 2>/dev/null; then
    echo "Prediction LLM started at http://127.0.0.1:8765/"
    exit 0
  fi
  attempt=$((attempt + 1))
  sleep 0.2
done

echo "Prediction LLM did not become ready; see $project_dir/logs/app.stderr.log" >&2
exit 1
