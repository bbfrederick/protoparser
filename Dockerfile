# Use bookworm as a base
FROM python:3.14-bookworm

# get build arguments
ARG BUILD_TIME
ARG BRANCH
ARG GITVERSION
ARG GITSHA
ARG GITDATE

# set and echo environment variables
ENV BUILD_TIME=$BUILD_TIME
ENV BRANCH=$BRANCH
ENV GITVERSION=${GITVERSION}
ENV GITSHA=${GITSHA}
ENV GITDATE=${GITDATE}

RUN echo "BRANCH: "$BRANCH
RUN echo "BUILD_TIME: "$BUILD_TIME
RUN echo "GITVERSION: "$GITVERSION
RUN echo "GITSHA: "$GITSHA
RUN echo "GITDATE: "$GITDATE

# set the shell to bash
SHELL [ "/bin/bash", "--login", "-c" ]

# Prepare the unix environment
ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=America/New_York
RUN apt-get update --fix-missing && \
    apt update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends tzdata && \
    apt-get install -y --no-install-recommends cgroup-tools && \
    apt-get install -y --no-install-recommends tesseract-ocr

# Pull in the newest versions of packages to address any security issues
RUN apt-get dist-upgrade -y 

# Clean up
RUN apt-get autoremove
RUN apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Set CPATH for packages relying on compiled libs (e.g. indexed_gzip)
ENV PATH="/root/.local/bin:$PATH" \
    LANG="C.UTF-8" \
    LC_ALL="C.UTF-8" \
    PYTHONNOUSERSITE=1

# install uv to make installations faster
RUN pip install uv

# Copy protoparser into container
COPY . /src/protoparser

# Install the package with the OCR extra. tesseract-ocr above is the native
# binary; the extra is the Python side (pytesseract, pillow). Both are needed
# for --ocr always, and neither implies the other.
RUN cd /src/protoparser && \
    uv tool install ".[ocr]"
RUN chmod -R a+r /src/protoparser

# clean up
RUN pip cache purge
RUN uv cache clean

ENV RUNNING_IN_CONTAINER=1

RUN cd /root; TZ=GMT date "+%Y-%m-%d %H:%M:%S" > buildtime

ARG VERSION
ARG BUILD_DATE
ARG VCS_REF

LABEL org.label-schema.build-date=$BUILD_DATE \
      org.label-schema.name="protoparser" \
      org.label-schema.description="Tools for manipulating Siemens PDF MR protocol files" \
      org.label-schema.url="http://nirs-fmri.net" \
      org.label-schema.vcs-ref=$VCS_REF \
      org.label-schema.vcs-url="https://github.com/bbfrederick/protoparser" \
      org.label-schema.version=$VERSION
