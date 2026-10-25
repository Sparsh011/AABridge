#!/bin/zsh
set -eu
cd -- "${0:A:h}"
trap 'if [[ -t 0 ]]; then print "\nPress any key to close."; read -k 1; fi' EXIT
print 'AABridge Mac mini setup'
print 'This installs the bundled bridge, prepares a disposable demo for the chosen worker'
print '(Codex by default, or --worker claude), checks it without model inference, and'
print 'adds a restricted demo-project SSH key.'
print 'If needed, it installs Python 3.11 using an existing Homebrew installation.'
print 'Per-agent app locations are read from runtime-paths.json in this folder.'
print 'Keep any selected external drive connected while its runtime is used.'

aabridge_setup_python=''
for aabridge_candidate in python3.11 python3.12 python3.13 python3.14 python3 /opt/homebrew/bin/python3.11 /usr/local/bin/python3.11; do
  if command -v "$aabridge_candidate" >/dev/null 2>&1 && "$aabridge_candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
    aabridge_setup_python="$(command -v "$aabridge_candidate")"
    break
  fi
done
if [[ -z "$aabridge_setup_python" ]]; then
  if [[ " ${*} " == *' --diagnose '* ]]; then
    print 'DIAGNOSTICS INCOMPLETE: Python 3.11+ is missing. Install receiver can use an existing Homebrew installation to prepare Python 3.11.'
    exit 1
  fi
  aabridge_setup_brew=''
  for aabridge_candidate in brew /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if command -v "$aabridge_candidate" >/dev/null 2>&1; then
      aabridge_setup_brew="$(command -v "$aabridge_candidate")"
      break
    fi
  done
  if [[ -z "$aabridge_setup_brew" ]]; then
    print 'Python 3.11+ is missing and Homebrew is unavailable. Install Python 3.11, then rerun this file.'
    exit 1
  fi
  "$aabridge_setup_brew" install python@3.11
  aabridge_setup_python="$("$aabridge_setup_brew" --prefix python@3.11)/bin/python3.11"
fi
"$aabridge_setup_python" ./setup_worker.py --bundle "$PWD" "$@"
