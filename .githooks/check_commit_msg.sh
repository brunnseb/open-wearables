#!/bin/sh
# Commit subjects name the bead that owns the work: bd-1a2b3c (see AGENTS.md, Issue tracking).
msg=$1
first_line=$(sed -n '1p' "$msg")

case $first_line in
Merge*|Revert*|fixup!*|squash!*|Bump*|Chore*) exit 0 ;;
esac

if ! printf '%s' "$first_line" | grep -Eq 'bd-[0-9a-z]+'; then
	echo "commit-msg: the subject must name a bead, e.g."
	echo "  fix(svix): bd-4ce share the database secret with the webhooks container"
	echo "Claim one with 'bd update <id> --claim', or create it with 'bd create \"...\" -p 1 -t task'."
	exit 1
fi
