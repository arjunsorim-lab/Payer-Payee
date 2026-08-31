#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
data_dir="$project_dir/data"
log_dir="$project_dir/logs"
service_label="com.codex.payer-payee.mongodb.27018"
service_domain="gui/$(id -u)"
service_target="$service_domain/$service_label"
plist_file="$project_dir/com.codex.payer-payee.mongodb.27018.plist"

mkdir -p "$data_dir" "$log_dir"

if launchctl print "$service_target" >/dev/null 2>&1; then
  launchctl kickstart -k "$service_target"
else
  launchctl bootstrap "$service_domain" "$plist_file"
fi

attempt=0
while [ "$attempt" -lt 30 ]; do
  if nc -z 127.0.0.1 27018 2>/dev/null; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 0.2
done

if ! nc -z 127.0.0.1 27018 2>/dev/null; then
  echo "MongoDB did not become ready; see $log_dir/mongod.log" >&2
  exit 1
fi

echo "MongoDB started on mongodb://127.0.0.1:27018/"
