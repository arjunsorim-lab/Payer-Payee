#!/bin/sh
set -eu

service_label="com.codex.payer-payee.prediction-llm.8765"
service_target="gui/$(id -u)/$service_label"

if launchctl print "$service_target" >/dev/null 2>&1; then
  launchctl bootout "$service_target"
  echo "Stopped the Prediction LLM service."
else
  echo "The Prediction LLM service is not running."
fi
