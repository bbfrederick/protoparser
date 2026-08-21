#!/bin/bash

DATADIR=/Users/frederic/code/protoparser

MYIPADDRESS=$(ifconfig en0 | grep 'inet ' | awk '{print $2}')
VERSION=latest

# allow network connections in Xquartz Security settings
xhost +

docker pull fredericklab/protoparser:${VERSION}

docker run \
    --rm \
    --ipc host \
    --mount type=bind,source=${DATADIR},destination=/data \
    -it \
    -v /tmp/.X11-unix:/tmp/.X11-unix \
    fredericklab/protoparser:${VERSION} \
    siemens-protocol \
        list \
        /data/examples/XA60/R01StressDynXA60.pdf
