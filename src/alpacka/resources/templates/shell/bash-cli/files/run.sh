#!/usr/bin/env bash
# {{PROJECT_NAME}} — entry point.
# Run via `./run.sh [-v] [-n NAME]` or via ALPACKA's Launch button.

set -euo pipefail

SCRIPT_NAME="$(basename "$0")"

usage() {
    cat <<EOF
Usage: $SCRIPT_NAME [-v] [-n NAME]

Options:
  -v          Verbose output
  -n NAME     Greet NAME (default: world)
  -h          Show this help

Environment variables:
  Read by the script automatically — set them on the project in
  alpacka.yml's variables: section, or pass them inline.
EOF
}

verbose=0
name="world"

while getopts ":vn:h" opt; do
    case "$opt" in
        v) verbose=1 ;;
        n) name="$OPTARG" ;;
        h) usage; exit 0 ;;
        :) echo "$SCRIPT_NAME: -$OPTARG requires a value" >&2; usage; exit 2 ;;
        \?) echo "$SCRIPT_NAME: unknown option -$OPTARG" >&2; usage; exit 2 ;;
    esac
done
shift $((OPTIND - 1))

[ "$verbose" -eq 1 ] && set -x

echo "hello, $name"

# Anything passed after the options gets forwarded as positional args.
for arg in "$@"; do
    echo "  positional: $arg"
done
