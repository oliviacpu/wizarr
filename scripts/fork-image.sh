#!/usr/bin/env bash
set -euo pipefail

image=ghcr.io/oliviacpu/wizarr
if [[ ${GITHUB_REPOSITORY:-} != oliviacpu/wizarr || ${GITHUB_REF:-} != refs/tags/oliviacpu-* || ! ${GITHUB_REF_NAME:-} =~ ^oliviacpu-[0-9]{4}\.[0-9]{2}\.[0-9]{2}(\.[1-9][0-9]*)?$ ]]; then
  echo 'Only dated oliviacpu/wizarr release tags can publish.' >&2
  exit 1
fi

git fetch --quiet --force --tags origin
git fetch --quiet origin main:refs/remotes/origin/main
if [[ $(git rev-parse "refs/tags/${GITHUB_REF_NAME}^{commit}") != "$GITHUB_SHA" ]]; then
  echo 'The release tag moved; refusing to publish.' >&2
  exit 1
fi
git merge-base --is-ancestor "$GITHUB_SHA" refs/remotes/origin/main

case ${1:-} in
  prepare)
    error_file=$(mktemp)
    trap 'rm -f "$error_file"' EXIT
    if docker pull --platform linux/amd64 "${image}:${GITHUB_REF_NAME}" 2>"$error_file"; then
      revision=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "${image}:${GITHUB_REF_NAME}")
      if [[ $revision != "$GITHUB_SHA" ]]; then
        echo 'This image tag already belongs to a different commit; it is immutable.' >&2
        exit 1
      fi
      echo 'exists=true' >> "$GITHUB_OUTPUT"
    elif grep -Eiq 'manifest unknown|manifest_unknown|not found' "$error_file"; then
      echo 'exists=false' >> "$GITHUB_OUTPUT"
    else
      cat "$error_file" >&2
      exit 1
    fi
    ;;
  latest)
    newest=$(git tag -l 'oliviacpu-*' --sort=-v:refname | grep -E '^oliviacpu-[0-9]{4}\.[0-9]{2}\.[0-9]{2}(\.[1-9][0-9]*)?$' | sed -n '1p')
    if [[ $newest != "$GITHUB_REF_NAME" ]]; then
      echo "::notice::${GITHUB_REF_NAME} is older than ${newest}; latest stays put."
      exit 0
    fi
    docker buildx imagetools create --tag "${image}:latest" "${image}:${GITHUB_REF_NAME}"
    ;;
  *) exit 1 ;;
esac
