#!/bin/sh
# kokabiel-sd: SD card helper. Installed as /usr/sbin/kokabiel-sd.
# Usage: kokabiel-sd find|describe|mount|is-mounted
# find: rc 1 if no safe SD card. is-mounted: rc 0 only if CAPTURE_DIR is mounted.
# Exit codes: 0 ok, 1 negative answer, 3 usage.

KK_LIB="${KOKABIEL_LIB:-$(dirname "$0")/lib}"
[ -d "$KK_LIB" ] || KK_LIB=/usr/libexec/kokabiel
# shellcheck source=lib/common.sh disable=SC1091
. "$KK_LIB/common.sh"
# shellcheck source=lib/sd.sh disable=SC1091
. "$KK_LIB/sd.sh"

case ${1:-} in
find) kk_sd_find ;;
describe) kk_sd_describe ;;
mount) kk_sd_mount ;;
is-mounted) kk_sd_is_mounted ;;
*) kk_die "usage: kokabiel-sd find|describe|mount|is-mounted" 3 ;;
esac
