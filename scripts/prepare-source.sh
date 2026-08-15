#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${repo_root}/upstream.env"

target="${1:-${repo_root}/.build/hermes-agent}"
if [[ -e "${target}" ]]; then
  echo "Target already exists: ${target}" >&2
  echo "Choose an empty path so an existing checkout is never overwritten." >&2
  exit 1
fi

mkdir -p "$(dirname "${target}")"
git init --quiet "${target}"
git -C "${target}" remote add origin "https://github.com/${UPSTREAM_REPOSITORY}.git"
git -C "${target}" fetch --quiet --depth 1 origin "${UPSTREAM_SHA}"
git -C "${target}" checkout --quiet --detach FETCH_HEAD

actual_sha="$(git -C "${target}" rev-parse HEAD)"
if [[ "${actual_sha}" != "${UPSTREAM_SHA}" ]]; then
  echo "Upstream SHA mismatch: expected ${UPSTREAM_SHA}, got ${actual_sha}" >&2
  exit 1
fi

shopt -s nullglob
patches=("${repo_root}"/patches/*.patch)
if (( ${#patches[@]} == 0 )); then
  echo "No patches found under ${repo_root}/patches" >&2
  exit 1
fi

for patch_file in "${patches[@]}"; do
  echo "Applying $(basename "${patch_file}")"
  git -C "${target}" apply --check "${patch_file}"
  git -C "${target}" apply "${patch_file}"
done

git -C "${target}" diff --check
printf '%s\n' "Prepared Hermes ${UPSTREAM_TAG} (${UPSTREAM_SHA}) in ${target}"
