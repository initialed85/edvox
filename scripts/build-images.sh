#!/bin/bash
# Build and push the three edvox images.
#
# Run from the repo root on an x86-64 box: august-desktop is amd64 and llama.cpp's
# build is not worth doing under emulation. If building from icarus, point DOCKER_HOST
# at a CI node (tcp://192.168.1.211:2375) instead.
#
# Images are :latest to match the rest of the dev cluster (imagePullPolicy: Always,
# rollout restart to pick a build up). Tag a immutable build number when something is
# worth keeping.
set -euo pipefail

cd "$(dirname "$0")/.."
REGISTRY=${REGISTRY:-docker.io/initialed85}
TAG=${TAG:-latest}

for component in tts whisper web; do
  echo "==> building $REGISTRY/edvox-$component:$TAG"
  docker build -f "docker/Dockerfile.$component" -t "$REGISTRY/edvox-$component:$TAG" .
done

if [ "${PUSH:-1}" != "0" ]; then
  for component in tts whisper web; do
    echo "==> pushing edvox-$component:$TAG"
    docker push "$REGISTRY/edvox-$component:$TAG"
  done
fi

docker images --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | grep "edvox-"
