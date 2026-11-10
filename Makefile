.PHONY: install verify uninstall purge-state lint test

install:
	./install.sh

verify:
	codex-isolated verify
	claude-isolated verify

uninstall:
	./uninstall.sh

purge-state:
	./uninstall.sh --purge-state

lint:
	@for script in install.sh uninstall.sh bin/codex-isolated tests/static.sh tests/launcher.sh tests/entrypoint.sh bin/claude-isolated; do \
		bash -n "$$script" || exit; \
	done
	@for script in entrypoint.sh images/claude/entrypoint.sh; do \
		sh -n "$$script" || exit; \
	done

test: lint
	./tests/static.sh
	./tests/launcher.sh
	bash ./tests/entrypoint.sh
	python3 tests/agents.py
	python3 tests/claude-entrypoint.py
