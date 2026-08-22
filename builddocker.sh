#!/bin/bash

set -ex

# SET THE FOLLOWING VARIABLES
# docker hub username
USERNAME=fredericklab
# image name
IMAGE=protoparser

# ensure we're up to date, and pick up any tags made elsewhere
git pull --tags

# The version is the git tag, so there is no VERSION file to keep in step and
# no number to bump by hand. A tagged commit gives a clean "0.2.0"; anything
# else gives "0.2.0-3-gabc1234", which is meant to look unreleased.
version=$(git describe --tags --always --dirty | sed 's/^v//')
if [ -z "$version" ]; then
    echo "could not determine a version from git; is this a checkout with tags?" >&2
    exit 1
fi
echo "version: $version"

# Only a clean tagged commit is a release. Anything else is tagged with its
# describe string alone and never moves :latest, so a local test build cannot
# overwrite the tag that testdocker.sh and end users pull.
tags="--tag $USERNAME/$IMAGE:$version"
if git describe --exact-match --tags HEAD >/dev/null 2>&1 && [ -z "$(git status --porcelain)" ]; then
    tags="$tags --tag $USERNAME/$IMAGE:latest"
    echo "tagged release: this build will move :latest"
else
    echo "not a clean tagged commit: building $version only, leaving :latest alone"
fi

# Publishing is opt-in. Run with PUSH=1 to send the result to Docker Hub.
# --load cannot accept a multi-platform build -- it writes into the local
# daemon, which holds one architecture per tag -- so a local build is native
# only and the full matrix is built when publishing.
if [ "${PUSH:-0}" = "1" ]; then
    destination="--push"
    platforms="--platform linux/arm64,linux/amd64"
else
    destination="--load"
    platforms=""
    echo "local build only; re-run with PUSH=1 to publish"
fi

# run build
docker buildx build . \
    $destination \
    $platforms \
    $tags \
    --build-arg VERSION=$version \
    --build-arg BUILD_DATE=`date +"%Y%m%dT%H%M%S"` \
    --build-arg GITVERSION=$GITVERSION \
    --build-arg GITDIRECTVERSION=$GITVERSION \
    --build-arg VCS_REF=`git rev-parse HEAD`
