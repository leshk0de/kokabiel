SH_FILES := $(wildcard scripts/*.sh scripts/lib/*.sh scripts/files/capture-prep scripts/files/init.d/* scripts/files/hotplug/*)

.PHONY: test lint tools shim

# shim: build the libpcap immediate-mode LD_PRELOAD shim (aarch64 musl) inside the
# test base image, so it matches the probe's ABI. Native on arm64 hosts; an x86
# host needs Docker's arm64 emulation (qemu/binfmt). Rebuilds only if the .c is newer.
SHIM_IMAGE := $(shell sed -n 's/^FROM //p' tests/Dockerfile)

build/pcap_immediate.so: scripts/files/pcap_immediate.c
	mkdir -p build
	docker run --rm -v "$(CURDIR)/scripts/files:/src:ro" -v "$(CURDIR)/build:/out" $(SHIM_IMAGE) \
		sh -c 'apk update >/dev/null && apk add gcc >/dev/null && gcc -shared -fPIC -O2 -s -o /out/pcap_immediate.so /src/pcap_immediate.c'

shim: build/pcap_immediate.so

test:
	uv run pytest -q

lint:
	@if [ -n "$(SH_FILES)" ]; then shellcheck -s sh $(SH_FILES); fi
	@if [ -d ansible ]; then ansible-lint ansible/; else echo "ansible/ not present yet; skipping ansible-lint"; fi

tools:
	brew install shellcheck
	uv tool install ansible-core
	uv tool install ansible-lint
