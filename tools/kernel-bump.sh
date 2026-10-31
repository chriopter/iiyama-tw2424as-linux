#!/bin/bash
# Upstream watch for the pinned kernel (.github/workflows/kernel-bump.yml, daily).
#   newest stable across all series: update system/kernel/VERSION (SHA256 from kernel.org's
#     signed sha256sums.asc), check that all patches apply (./build.sh fetch kernel = git am on the new
#     tarball), then push branch kernel-bump/<version> + open a pull request and start a build of it.
#     A passed build merges the pull request and releases from main (.github/workflows/kernel.yml);
#     a failed build leaves it open. Patches do not apply -> issue and failed check.
#   Release candidates and end-of-life releases are excluded. Series changes use the same checks.
#
#   tools/kernel-bump.sh [--dry-run]     --dry-run: check + apply locally, no git push/PR/issue
set -euo pipefail
P=$(cd "$(dirname "$0")/.." && pwd)
DRY=0
[ "${1:-}" = --dry-run ] && DRY=1
cd "$P"

gh_run() { if [ $DRY = 1 ]; then echo "dry-run: gh $*"; else gh "$@"; fi; }

# issue TITLE BODY: open once (also not again if an issue with the title was closed)
issue() {
	if [ $DRY = 0 ] && [ -n "$(gh issue list --state all --search "\"$1\" in:title" --json number --jq '.[].number')" ]; then
		echo "issue exists: $1"
	else
		gh_run issue create --title "$1" --body "$2"
	fi
}

# shellcheck source=system/kernel/VERSION
. system/kernel/VERSION
CUR=$VERSION
read -r NEW < <(curl -fsSL https://www.kernel.org/releases.json | python3 -c '
import json, sys
cur = sys.argv[1]
ver = lambda v: tuple(int(x) for x in v.split("."))
rel = [r for r in json.load(sys.stdin)["releases"]
       if r["moniker"] in ("stable", "longterm") and not r.get("iseol", False)]
new = max((r["version"] for r in rel), key=ver, default=cur)
print(new if ver(new) > ver(cur) else "-")
' "$CUR")
echo "pinned $CUR; newer stable: $NEW"

[ "$NEW" != - ] || { echo "no newer stable release"; exit 0; }

BR=kernel-bump/$NEW
if [ $DRY = 0 ] && git ls-remote --exit-code --heads origin "$BR" > /dev/null; then
	echo "branch $BR exists already"; exit 0
fi

# SHA256 from kernel.org's sha256sums.asc, signed by the kernel.org checksum autosigner (key via WKD)
MAJ=${NEW%%.*}
BASE=https://cdn.kernel.org/pub/linux/kernel/v$MAJ.x
G=$(mktemp -d) && trap 'rm -rf "$G"' EXIT
export GNUPGHOME=$G/gnupg && mkdir -m 700 "$GNUPGHOME"
curl -fsSL -o "$G/sha256sums.asc" "$BASE/sha256sums.asc"
gpg -q --auto-key-locate clear,wkd --locate-keys autosigner@kernel.org > /dev/null 2>&1 || { echo "error: cannot fetch the kernel.org autosigner key" >&2; exit 1; }
gpg -q --verify "$G/sha256sums.asc" 2> "$G/gpg.log" || { cat "$G/gpg.log" >&2; echo "error: sha256sums.asc signature invalid" >&2; exit 1; }
grep -q 'Good signature from "Kernel.org checksum autosigner <autosigner@kernel.org>"' "$G/gpg.log" || { cat "$G/gpg.log" >&2; exit 1; }
SHA=$(gpg -q --decrypt "$G/sha256sums.asc" 2> /dev/null | awk -v f="linux-$NEW.tar.xz" '$2 == f {print $1}')
# releases.json lists a new release before the autosigner has added it to sha256sums.asc (can lag hours):
# not an error, the next daily run picks it up
[ ${#SHA} = 64 ] || { echo "::notice::linux-$NEW.tar.xz not yet in sha256sums.asc, retry next run"; exit 0; }

printf 'VERSION=%s\nURL=%s/linux-%s.tar.xz\nSHA256=%s\n' "$NEW" "$BASE" "$NEW" "$SHA" > system/kernel/VERSION
echo "system/kernel/VERSION -> $NEW ($SHA)"

rm -rf build/src/linux
if ! ./build.sh fetch kernel > "$G/fetch.log" 2>&1; then
	cat "$G/fetch.log"
	[ $DRY = 1 ] && git checkout -q system/kernel/VERSION
	issue "Linux $NEW: patches do not apply" "Linux $NEW is out (pinned: $CUR), but \`./build.sh fetch kernel\` fails:
\`\`\`
$(tail -40 "$G/fetch.log")
\`\`\`
Rebase the patches (build/src/linux, then ./build.sh export kernel) and update system/kernel/VERSION."
	echo "::error::Linux $NEW: kernel fetch or patch application failed (see issue and log above)" >&2
	exit 1
fi
echo "all patches apply on $NEW"

if [ $DRY = 1 ]; then
	echo "dry-run: would push $BR, open a pull request and build it"
	git diff --stat system/kernel/VERSION
	git checkout -q system/kernel/VERSION
	exit 0
fi
git switch -c "$BR"
git add system/kernel/VERSION
git commit -q -m "kernel: update to Linux $NEW" -m "kernel.org stable $NEW (was $CUR); all patches apply (git am)."
git push -q origin "$BR"
gh pr create --base main --head "$BR" --title "kernel: update to Linux $NEW" --body "Automatic upstream check: kernel.org stable **$NEW** (pinned: $CUR).

- \`system/kernel/VERSION\`: $NEW, SHA256 from kernel.org's signed \`sha256sums.asc\`
- all patches in \`system/kernel/patches/\` apply (\`git am\` on the new tarball)
- a build of this branch runs as workflow \`kernel\` (build only, no release)

A passed build merges this pull request (older bump pull requests are closed) and publishes release
\`kernel-$NEW-iiyama-…\` (workflow \`kernel\` on main); panels then offer it in \`wallpanel-update check\` and
install it with the slot-B test + health check. A failed build leaves it open."
gh workflow run kernel.yml --ref "$BR"
