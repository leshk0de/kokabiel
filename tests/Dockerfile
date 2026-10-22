# Test image for the Kokabiel on-probe scripts: the OpenWrt 25.12.2 rootfs
# (BusyBox ash, uci, apk). conftest.py builds this once, tagged by a hash of
# this file, so appending packages below triggers a rebuild.
#
# openwrt/rootfs:armsr-armv8-v25.12.2 is the intended tag, but Docker Hub's
# manifest list labels it platform "aarch64_generic", which Docker cannot
# resolve, so a plain pull fails. Pinning the platform manifest by digest works.
FROM openwrt/rootfs@sha256:fedf8ac03f89452ef13585c586dd6a13bcc4d9fc9fc8d0103674da9f5f1380e9

RUN apk update && apk add softflowd
RUN apk update && apk add prometheus-node-exporter-lua
