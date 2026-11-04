.PHONY: help run lint clean

help:                    ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'

run:                     ## Run the script with default args.
	./run.sh

lint:                    ## Run shellcheck if available.
	@command -v shellcheck >/dev/null 2>&1 \
		&& shellcheck run.sh \
		|| echo "shellcheck not installed; skipping"

clean:                   ## Remove generated artifacts (placeholder).
	@echo "nothing to clean — add your own targets here"
