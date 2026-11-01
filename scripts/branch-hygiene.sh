#!/usr/bin/env bash
set -euo pipefail

# A squash merge applies the changes but does not preserve the source commit
# graph. Archive the exact, verified merged PR tip before deleting its branch.
mode="sweep"
branch_arg=""
if (( $# > 0 )); then mode="$1"; fi
if (( $# > 1 )); then branch_arg="$2"; fi
repo="$GITHUB_REPOSITORY"
if [[ "$repo" != "ordaxsystems/ordax-runtime" ]]; then
  echo "refuse: cleanup is exclusively owned by ordax-runtime" >&2
  exit 2
fi
owner="ordaxsystems"

is_transient_branch() {
  case "$1" in
    feat/*|fix/*|refactor/*|ci/*|integration/*|codex/*|docs/*|blender-bridge) return 0 ;;
    *) return 1 ;;
  esac
}

is_protected_branch() {
  case "$1" in
    main|archive/*) return 0 ;;
    *) return 1 ;;
  esac
}

# API failures must NEVER be interpreted as absence of open PRs.
open_pr_count() {
  local count
  if ! count="$(gh api "repos/$repo/pulls?state=open&head=$owner:$1&per_page=100" --jq 'length')"; then
    return 2
  fi
  [[ "$count" =~ ^[0-9]+$ ]] || return 2
  printf '%s\n' "$count"
}

# A historical PR from an earlier branch tip does not authorize deletion.
# Require exact HEAD, both repository owners and a merge ancestor in main.
exact_merged_pr_proof() {
  local branch="$1"
  local branch_sha="$2"
  local rows
  if ! rows="$(gh api --paginate "repos/$repo/pulls?state=closed&head=$owner:$branch&per_page=100" --jq '.[] | select(.merged_at != null) | "\(.head.sha)|\(.merge_commit_sha)|\(.base.ref)|\(.head.repo.full_name)|\(.base.repo.full_name)"')"; then
    return 2
  fi
  local head_sha merge_sha base_ref head_repo base_repo
  local matches=0
  local accepted_merge=""
  while IFS='|' read -r head_sha merge_sha base_ref head_repo base_repo; do
    [[ -z "$head_sha" ]] && continue
    if [[ "$head_sha" != "$branch_sha" || "$base_ref" != "main" ||
          "$head_repo" != "$repo" || "$base_repo" != "$repo" ||
          ! "$merge_sha" =~ ^[0-9a-f]{40}$ ]]; then
      continue
    fi
    ((matches += 1))
    if git merge-base --is-ancestor "$merge_sha" origin/main; then
      accepted_merge="$merge_sha"
    fi
  done <<< "$rows"
  if (( matches != 1 )) || [[ -z "$accepted_merge" ]]; then
    return 1
  fi
  printf '%s\n' "$accepted_merge"
}

has_exact_archive_snapshot() {
  local branch_sha="$1"
  local archive_ref archive_sha
  while IFS= read -r archive_ref; do
    [[ -z "$archive_ref" ]] && continue
    archive_sha="$(git rev-parse "$archive_ref")"
    if [[ "$archive_sha" == "$branch_sha" ]]; then
      return 0
    fi
  done < <(git for-each-ref --format='%(refname:short)' refs/remotes/origin/archive/)
  return 1
}

ensure_archived_if_divergent() {
  local branch="$1"
  local branch_sha="$2"
  if git merge-base --is-ancestor "$branch_sha" origin/main; then
    return 0
  fi
  if has_exact_archive_snapshot "$branch_sha"; then
    echo "preserved $branch: exact archive branch already contains tip"
    return 0
  fi
  local archive_tag="archive/ordax-runtime/$branch"
  local tag_sha
  if ! tag_sha="$(git ls-remote --refs origin "refs/tags/$archive_tag" | awk 'NR == 1 { print $1 }')"; then
    return 1
  fi
  if [[ -z "$tag_sha" ]]; then
    # No force push, no overwriting a tag that belongs to another commit.
    if ! git push origin "$branch_sha:refs/tags/$archive_tag"; then
      return 1
    fi
  elif [[ "$tag_sha" != "$branch_sha" ]]; then
    echo "skip $branch: archive tag has a different owner" >&2
    return 1
  fi
  if ! tag_sha="$(git ls-remote --refs origin "refs/tags/$archive_tag" | awk 'NR == 1 { print $1 }')"; then
    return 1
  fi
  [[ "$tag_sha" == "$branch_sha" ]] || return 1
  echo "archived $branch at $archive_tag"
}

delete_branch_if_safe() {
  local branch="$1"
  if ! is_transient_branch "$branch" || is_protected_branch "$branch"; then
    echo "skip $branch: protected or non-transient ref"
    return 0
  fi
  if ! git show-ref --verify --quiet "refs/remotes/origin/$branch"; then
    echo "skip $branch: remote branch no longer exists"
    return 0
  fi
  local branch_sha open_count merged_commit
  branch_sha="$(git rev-parse "origin/$branch")"
  if [[ ! "$branch_sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "skip $branch: branch tip is not verified"
    return 0
  fi
  if ! open_count="$(open_pr_count "$branch")"; then
    echo "skip $branch: open PR query unavailable"
    return 0
  fi
  if [[ "$open_count" != "0" ]]; then
    echo "skip $branch: open PR exists"
    return 0
  fi
  if ! merged_commit="$(exact_merged_pr_proof "$branch" "$branch_sha")"; then
    echo "skip $branch: no exact merged PR head with a merge ancestor in main"
    return 0
  fi
  if ! ensure_archived_if_divergent "$branch" "$branch_sha"; then
    echo "skip $branch: source history archive is unverified"
    return 0
  fi
  # Git push --delete has no compare-and-swap. Recheck the live ref and open
  # PR state immediately before deletion; do not claim atomicity.
  local current_sha
  if ! current_sha="$(git ls-remote --heads origin "refs/heads/$branch" | awk 'NR == 1 { print $1 }')"; then
    echo "skip $branch: branch recheck unavailable"
    return 0
  fi
  if [[ "$current_sha" != "$branch_sha" ]]; then
    echo "skip $branch: head changed since verification"
    return 0
  fi
  if ! open_count="$(open_pr_count "$branch")" || [[ "$open_count" != "0" ]]; then
    echo "skip $branch: open PR state changed or is unknown"
    return 0
  fi
  echo "retire $branch: merged=$merged_commit source=$branch_sha (history preserved)"
  git push origin --delete "$branch"
}

git fetch --prune origin "+refs/heads/*:refs/remotes/origin/*"
case "$mode" in
  branch)
    [[ -n "$branch_arg" ]] || { echo "branch mode needs a branch name" >&2; exit 2; }
    delete_branch_if_safe "$branch_arg"
    ;;
  sweep)
    while IFS= read -r remote_ref; do
      [[ "$remote_ref" == "origin/HEAD" ]] && continue
      branch="$(printf '%s\n' "$remote_ref" | sed 's@^origin/@@')"
      delete_branch_if_safe "$branch"
    done < <(git for-each-ref --format='%(refname:short)' refs/remotes/origin | sort)
    ;;
  *)
    echo "usage: $0 [branch <name>|sweep]" >&2
    exit 2
    ;;
esac
