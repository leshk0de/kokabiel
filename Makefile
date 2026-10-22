SH_FILES := $(wildcard scripts/*.sh scripts/lib/*.sh scripts/files/capture-prep scripts/files/hotplug/*)

.PHONY: test lint tools

test:
	uv run pytest -q

lint:
	@if [ -n "$(SH_FILES)" ]; then shellcheck -s sh $(SH_FILES); fi
	@if [ -d ansible ]; then ansible-lint ansible/; else echo "ansible/ not present yet; skipping ansible-lint"; fi

tools:
	brew install shellcheck
	uv tool install ansible-core
	uv tool install ansible-lint
