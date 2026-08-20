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

fetch_ok=0
for attempt in 1 2 3 4 5; do
  if git -C "${target}" fetch --depth 1 origin "${UPSTREAM_SHA}"; then
    fetch_ok=1
    break
  fi
  echo "Upstream fetch attempt ${attempt} failed; retrying..." >&2
  sleep $((attempt * 15))
done

if [[ "${fetch_ok}" -eq 1 ]]; then
  git -C "${target}" checkout --quiet --detach FETCH_HEAD
  actual_sha="$(git -C "${target}" rev-parse HEAD)"
  if [[ "${actual_sha}" != "${UPSTREAM_SHA}" ]]; then
    echo "Upstream SHA mismatch: expected ${UPSTREAM_SHA}, got ${actual_sha}" >&2
    exit 1
  fi
else
  echo "git fetch failed; falling back to source archive" >&2
  archive_url="https://github.com/${UPSTREAM_REPOSITORY}/archive/${UPSTREAM_SHA}.tar.gz"
  tmp_tgz="$(mktemp)"
  curl -fsSL --retry 5 --retry-delay 10 -o "${tmp_tgz}" "${archive_url}"
  tar -xzf "${tmp_tgz}" -C "$(dirname "${target}")"
  rm -f "${tmp_tgz}"
  extracted="$(dirname "${target}")/${UPSTREAM_REPOSITORY##*/}-${UPSTREAM_SHA}"
  rm -rf "${target}"
  mv "${extracted}" "${target}"
  git init --quiet "${target}"
  git -C "${target}" add -A
  git -C "${target}" \
    -c user.name="hermes-cpa-ci" \
    -c user.email="hermes-cpa-ci@users.noreply.github.com" \
    commit --quiet -m "upstream ${UPSTREAM_SHA}"
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
