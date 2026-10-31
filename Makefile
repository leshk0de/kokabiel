SH_FILES := $(wildcard scripts/*.sh scripts/lib/*.sh scripts/files/capture-prep scripts/files/init.d/* scripts/files/hotplug/*)

.PHONY: test lint tools shim diagrams

# shim: build the libpcap immediate-mode LD_PRELOAD shim (aarch64 musl) inside the
# host emulates arm64 (needs qemu/binfmt). The base image manifest is labelled
# linux/aarch64_generic, the only platform name Docker accepts for it.
# Rebuilds only if the .c is newer.
# host emulates arm64 (--platform linux/aarch64_generic; needs qemu/binfmt) (qemu/binfmt). Rebuilds only if the .c is newer.
SHIM_IMAGE := $(shell sed -n 's/^FROM //p' tests/Dockerfile)

build/pcap_immediate.so: scripts/files/pcap_immediate.c
	mkdir -p build
	docker run --rm --platform linux/aarch64_generic -v "$(CURDIR)/scripts/files:/src:ro" -v "$(CURDIR)/build:/out" $(SHIM_IMAGE) \
		sh -c 'apk update >/dev/null && apk add gcc >/dev/null && gcc -shared -fPIC -O2 -s -o /out/pcap_immediate.so /src/pcap_immediate.c'

shim: build/pcap_immediate.so

# diagrams: render docs/diagrams/*.d2 to SVG (light and dark theme in one file).
# The SVGs are committed so GitHub can show them; rerun after editing a .d2.
D2_FLAGS := --layout=elk --theme=0 --dark-theme=200 --pad=24
DIAGRAMS := $(patsubst %.d2,%.svg,$(wildcard docs/diagrams/*.d2))

docs/diagrams/%.svg: docs/diagrams/%.d2
	d2 $(D2_FLAGS) $< $@

diagrams: $(DIAGRAMS)

test:
	uv run pytest -q

lint:
	@if [ -n "$(SH_FILES)" ]; then shellcheck -s sh $(SH_FILES); fi
	@if [ -d ansible ]; then (cd ansible && ANSIBLE_LOG_PATH=/dev/null ansible-lint); else echo "ansible/ not present yet; skipping ansible-lint"; fi

tools:
	brew install shellcheck d2 hudochenkov/sshpass/sshpass
	uv tool install ansible-core
	uv tool install ansible-lint
