#!/usr/bin/env bash
# Called only by default-branch update workflows. Never force-push.
set -euo pipefail
case "${1:-}" in
  data/metars.json|data/atis.json|data/runways.json|data/notams.json) ;;
  *) echo 'Unexpected publication path'; exit 1 ;;
esac
if [[ "${GITHUB_REF:-}" != "refs/heads/${DEFAULT_BRANCH:?}" ]]; then
  echo 'Publication is restricted to the default branch'; exit 1
fi
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git add -- "$1"
if ! git diff --cached --quiet; then
  git commit -m "Update ${1#data/} snapshot"
  for attempt in 1 2 3; do
    git fetch origin "$DEFAULT_BRANCH"
    # Disjoint data-file updates rebase safely; conflicts stop publication.
    git rebase "origin/$DEFAULT_BRANCH"
    if git push origin "HEAD:refs/heads/$DEFAULT_BRANCH"; then
      echo 'published=true' >> "$GITHUB_OUTPUT"
      exit 0
    fi
    sleep "$((attempt * 2))"
  done
  echo 'Publication failed after bounded retries'; exit 1
fi
echo 'published=true' >> "$GITHUB_OUTPUT"
