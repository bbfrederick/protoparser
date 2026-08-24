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
# else gives "0.2.1.dev3+gabc1234", which is meant to look unreleased.
#
# Ask setuptools-scm rather than reshaping `git describe` ourselves. Its answer
# is PEP 440 and describe's is not, and the difference is not cosmetic: the
# string goes to the build backend as SETUPTOOLS_SCM_PRETEND_VERSION_FOR_..., a
# describe string reaches packaging.version.Version, and InvalidVersion aborts
# the install inside the image. Passing describe output here did not produce an
# unreleased-looking image, it produced no image at all. Asking setuptools-scm
# also means a local image and a source install of the same commit can never
# disagree about their version.
version=$(python3 -m setuptools_scm 2>/dev/null) \
    || version=$(uv run --no-project --quiet --with "setuptools-scm>=8" \
                 python -m setuptools_scm 2>/dev/null) \
    || version=""
if [ -z "$version" ]; then
    echo "could not determine a version: this needs a checkout with tags, and" >&2
    echo "setuptools-scm available to python3 (pip install setuptools-scm) or uv" >&2
    exit 1
fi
echo "version: $version"

# The Docker tag cannot be that string as-is: every untagged version carries a
# local segment, and '+' is not a legal character in a tag. A clean release has
# no '+' and so is tagged with its bare number, exactly as before.
tag=${version//+/_}

# Only a clean tagged commit is a release. Anything else is tagged with its
# development version alone and never moves :latest, so a local test build
# cannot overwrite the tag that testdocker.sh and end users pull.
tags="--tag $USERNAME/$IMAGE:$tag"
if git describe --exact-match --tags HEAD >/dev/null 2>&1 && [ -z "$(git status --porcelain)" ]; then
    tags="$tags --tag $USERNAME/$IMAGE:latest"
    echo "tagged release: this build will move :latest"
else
    echo "not a clean tagged commit: building $tag only, leaving :latest alone"
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
