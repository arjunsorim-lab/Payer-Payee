#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
service_label="com.codex.payer-payee.mongodb.27018"
service_domain="gui/$(id -u)"
service_target="$service_domain/$service_label"

if ! launchctl print "$service_target" >/dev/null 2>&1; then
  echo "The isolated MongoDB service is not running."
  exit 0
fi

launchctl bootout "$service_target"
echo "Stopped the isolated MongoDB service."
