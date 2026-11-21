#!/bin/sh
# Make sure .env has a JWT_SECRET of at least 32 characters (the API refuses to start without one).
# Keeps an existing good secret, replaces a short or empty one, and adds the line if it is missing.
set -eu

ENV_FILE="${1:-.env}"

if grep -q '^JWT_SECRET=.\{32,\}' "$ENV_FILE"; then
  exit 0
fi

SECRET="$(openssl rand -hex 32)"

if grep -q '^JWT_SECRET=' "$ENV_FILE"; then
  perl -pi -e "s/^JWT_SECRET=.*/JWT_SECRET=$SECRET/" "$ENV_FILE"
else
  printf 'JWT_SECRET=%s\n' "$SECRET" >> "$ENV_FILE"
fi
echo "Generated a JWT_SECRET in $ENV_FILE"
