#!/bin/sh
# Run named test files on Linux (WSL Ubuntu) at a committed ref.
#
# Usage: .github/scripts/run_on_linux.sh <ref> <test file>...
#
#   <ref>   any committed ref or sha (HEAD, a branch, 3c89a19). It is resolved
#           here, on the Windows side, before WSL is entered, so a literal HEAD
#           means this checkout's HEAD and not the Linux clone's. Uncommitted
#           edits are not seen: commit first.
#   <file>  test paths relative to the repo root.
#
# Prints pytest's own output, then three lines prefixed "linux: ": the resolved
# ref, the platform line and pytest's summary line. The exit status is pytest's
# (2 for a usage or setup fault).
#
# The tests run in a Linux clone at ~/rn-linux inside WSL, never in a venv under
# /mnt/c (that would overwrite the Windows .venv, and the Windows working copy is
# CRLF). The clone fetches this repo's branches, checks out the sha detached,
# runs `uv sync --frozen` (a no-op when the lock is unchanged) and then pytest.
# `.shipyard` is absent in the clone, so the data-dir gates skip there, as on CI.
#
# One-time setup, inside WSL:
#   curl -LsSf https://astral.sh/uv/install.sh | sh
#   git clone /mnt/c/xtopher/code/retsaf-nur ~/rn-linux
#   cd ~/rn-linux && ~/.local/bin/uv sync --frozen --all-packages --all-groups --extra garmin --python 3.12
#
# Measured on 2026-10-26: the setup took about 64 s (uv install 2 s, clone and
# first sync with the Python 3.12 download 62 s); a warm run of two test files
# (45 tests, the lock unchanged) took 8 to 19 s of wall time, pytest 6 to 11 s of it.

set -u

usage() {
    sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 2
}

[ $# -ge 2 ] || usage
ref=$1
shift

sha=$(git rev-parse --verify --quiet "$ref^{commit}") || {
    echo "linux: '$ref' is not a committed ref in this repository" >&2
    exit 2
}
echo "linux: ref $sha"

# The repository's shared git dir, in the form WSL sees it (/mnt/<drive>/...).
common=$(git rev-parse --path-format=absolute --git-common-dir) || exit 2
case $common in
    [A-Za-z]:/*)
        drive=$(printf '%s' "$common" | cut -c1 | tr 'A-Z' 'a-z')
        src="/mnt/$drive/$(printf '%s' "$common" | cut -c4-)"
        ;;
    /mnt/*)
        src=$common
        ;;
    /[A-Za-z]/*)
        src="/mnt$common"
        ;;
    *)
        echo "linux: cannot map git dir '$common' to a WSL path" >&2
        exit 2
        ;;
esac

# Inside WSL: $1 = sha, $2 = source git dir, the rest = test files.
inner='
set -u
sha=$1; src=$2; shift 2
clone=$HOME/rn-linux
uv=$HOME/.local/bin/uv
if [ ! -d "$clone/.git" ] || [ ! -x "$uv" ]; then
    echo "linux: no clone at $clone or no uv at $uv; run the one-time setup in this script header" >&2
    exit 2
fi
cd "$clone" || exit 2
git fetch -q "$src" "+refs/heads/*:refs/remotes/win/*" || exit 2
if ! git cat-file -e "$sha^{commit}" 2>/dev/null; then
    git fetch -q "$src" "$sha" || { echo "linux: $sha is not reachable from a branch of $src" >&2; exit 2; }
fi
git checkout -q --force --detach "$sha" || exit 2
"$uv" sync -q --frozen --all-packages --all-groups --extra garmin --python 3.12 || exit 2
out=$(.venv/bin/python -m pytest "$@" -q -p no:cacheprovider -rA 2>&1)
rc=$?
printf "%s\n" "$out"
echo "linux: $(.venv/bin/python -c "import platform; print(platform.platform())")"
summary=$(printf "%s\n" "$out" | grep -E "(passed|failed|error|errors|skipped|no tests ran)( |,|$)" | grep -E " in [0-9.]+s" | tail -1)
echo "linux: summary: ${summary:-no pytest summary line}"
exit $rc
'

MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' wsl -e bash -c "$inner" bash "$sha" "$src" "$@"
