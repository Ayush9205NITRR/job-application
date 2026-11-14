#!/usr/bin/env bash
# One command to set everything up. Safe to run again: it only does what is still missing.
#
#   ./setup.sh
#
# Needs Python 3.9+ and a .env with GRIST_URL, GRIST_API_KEY and GRIST_DOC_ID.
# Docker is NOT needed — Grist can live at docs.getgrist.com.
set -u

cd "$(dirname "$0")" || exit 1
step=0
say() { step=$((step + 1)); printf '\n\033[1m%s. %s\033[0m\n' "$step" "$1"; }
ok() { printf '   ✓ %s\n' "$1"; }
die() { printf '\n\033[31m✗ %s\033[0m\n' "$1" >&2; shift; for l in "$@"; do printf '   %s\n' "$l" >&2; done; exit 1; }

# ---------------------------------------------------------------- python
say "Python"
PY=$(command -v python3 || command -v python) || die "No Python found." \
  "Install Python 3.9 or newer from https://www.python.org/downloads/"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || \
  die "Python 3.9+ is needed; found $("$PY" --version 2>&1)." \
      "Install a newer Python from https://www.python.org/downloads/"
ok "$("$PY" --version 2>&1)"

# ---------------------------------------------------------------- .env
say "Credentials"
if [ ! -f .env ]; then
  cp .env.example .env
  die "I made a .env for you — now fill in three lines, then run ./setup.sh again." \
      "" \
      "  GRIST_URL=https://docs.getgrist.com" \
      "  GRIST_API_KEY=..." \
      "  GRIST_DOC_ID=..." \
      "" \
      "Get both from docs.getgrist.com: make an empty document called Outreach," \
      "take the id out of its URL, then your name (top right) > Profile Settings > API Key."
fi
missing=
for name in GRIST_URL GRIST_API_KEY GRIST_DOC_ID; do
  grep -qE "^${name}=.+" .env || missing="$missing $name"
done
[ -z "$missing" ] || die "These are still blank in .env:$missing" \
  "Open .env in any editor and fill them in, then run ./setup.sh again."
ok "$(grep -E '^GRIST_URL=' .env | cut -d= -f2-)"

# ---------------------------------------------------------------- packages
say "Packages"
[ -d .venv ] || "$PY" -m venv .venv || die "Could not create the .venv folder."
VPY=.venv/bin/python
[ -x "$VPY" ] || VPY=.venv/Scripts/python.exe       # Windows layout
"$VPY" -m pip install -q --upgrade pip
"$VPY" -m pip install -q -r requirements.txt || die "pip could not install the packages."
ok "installed into .venv"

# ---------------------------------------------------------------- tests
say "Checking the code works"
for t in tests/test_all.py tests/test_ui.py tests/test_tracker.py; do
  "$VPY" -I "$t" >/dev/null || die "$t failed." "Run it yourself to see why:  .venv/bin/python $t"
done
ok "all tests pass"

# ---------------------------------------------------------------- grist
say "Grist tables"
"$VPY" -I scripts/setup_grist.py || die "Could not set up the tables." \
  "Check GRIST_URL, GRIST_API_KEY and GRIST_DOC_ID in .env."

# ---------------------------------------------------------------- import
say "Importing"
shopt -s nullglob
books=(imports/*.xlsx)
sheets=(imports/*.csv)
if [ ${#books[@]} -eq 0 ] && [ ${#sheets[@]} -eq 0 ]; then
  ok "nothing in imports/ yet — drop your workbook or Apollo CSV there and run this again"
else
  for f in "${books[@]}"; do
    printf '   %s\n' "$f"
    "$VPY" -I scripts/import_tracker.py --file "$f" || die "Import of $f failed."
  done
  [ ${#sheets[@]} -eq 0 ] || "$VPY" -I scripts/refresh.py --apply --skip-gmail || true
fi

# ---------------------------------------------------------------- pages
say "Dashboard pages"
"$VPY" -I scripts/make_pages.py || die "Could not build the pages."

# ---------------------------------------------------------------- done
URL=$(grep -E '^GRIST_URL=' .env | cut -d= -f2-)
DOC=$(grep -E '^GRIST_DOC_ID=' .env | cut -d= -f2-)
case "$URL" in
  *localhost*|*127.0.0.1*) WHERE="  ${URL%/}/o/docs/doc/${DOC}" ;;
  *)                       WHERE="  ${URL%/}  -> open the Outreach document (id ${DOC})" ;;
esac
cat <<EOF

────────────────────────────────────────────────────────
Done. Open your document:

${WHERE}

One click left, once: Founders table > Company column > right panel >
SHOW COLUMN = Company. Same for Founder in Outreach_Log.

From now on:
  .venv/bin/python scripts/refresh.py --apply     pick up new files + check Gmail
  .venv/bin/python scripts/make_pages.py          rebuild the pages

Gmail and the 3x-a-day schedule are optional — see LAPTOP_SETUP.md.
────────────────────────────────────────────────────────
EOF
