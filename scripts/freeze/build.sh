#!/usr/bin/env bash
# Static demo build (no backend): freeze a data slice, then build the Astro UI with every page prerendered.
# Usage: [FREEZE_BASE=/demos/swarm-board/] scripts/freeze/build.sh [OUT]   (default OUT dist-static/; local only, never publishes)
# Serve: mkdir -p /tmp/sb/demos && ln -sfn OUT/site /tmp/sb/demos/swarm-board && python3 -m http.server 8791 -d /tmp/sb
#        then open http://localhost:8791/demos/swarm-board/   (FREEZE_BASE=/ serves OUT/site at the root instead)
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd)
OUT=${1:-$REPO/dist-static}
CFG=${FREEZE_CONFIG:-$REPO/scripts/freeze/slice.json}
# Site base path: the demo lives under /demos/swarm-board/ on a personal site.
BASE=${FREEZE_BASE:-/demos/swarm-board/}
WORK=$(mktemp -d /tmp/freeze-build.XXXXXX)
trap 'rm -rf "$WORK"' EXIT

python3 "$REPO/scripts/freeze/freeze_data.py" --root "$REPO" --config "$CFG" --out "$OUT/_freeze"

# Build copy: the slice's stage dir plays ROOT/data, so build-time reads see only the slice.
mkdir -p "$WORK/ui"
rsync -a --exclude node_modules --exclude dist --exclude .astro --exclude tests --exclude 'public/data' --exclude 'public/artifacts' --exclude 'public/gamejam-media' "$REPO/ui/" "$WORK/ui/"
ln -s "$REPO/ui/node_modules" "$WORK/ui/node_modules"
# Freeze-only behaviour patched into the build copy (dev UI untouched): no org selector or ?org= param.
cat "$REPO/scripts/freeze/no-org.js" >> "$WORK/ui/src/lib/frozen-fetch.js"
ln -s "$OUT/_freeze/stage/data" "$WORK/data"
mkdir -p "$WORK/ui/public"
cp -r "$OUT/_freeze/public/data" "$OUT/_freeze/public/frozen" "$WORK/ui/public/"

# Keep the demo pages (slice.json "pages" + index + eval schema routes) and the one prerenderable API (run list).
PAGES=$(python3 -c "import json,sys;print(' '.join(json.load(open(sys.argv[1]))['pages']))" "$CFG")
cd "$WORK/ui/src/pages"
for f in *.astro; do n=${f%.astro}; [[ " $PAGES index " == *" $n "* ]] || rm -f "$f"; done
find . -mindepth 1 -maxdepth 1 -type d ! -name api ! -name eval -exec rm -rf {} +
find api -type f ! -path api/v02/runs.ts -delete
grep -rl "export const prerender *= *false" . | xargs -r sed -i -E 's/export const prerender *= *false/export const prerender = true/'

RUNS=$(python3 -c "import json,sys;print(','.join(json.load(open(sys.argv[1]))['v02_runs']))" "$CFG")
DEF=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['default_run'])" "$CFG")
cd "$WORK/ui"
rm -rf "$OUT/site"
FREEZE_BASE="$BASE" PUBLIC_FROZEN=1 PUBLIC_FROZEN_RUNS="$RUNS" PUBLIC_FROZEN_DEFAULT_RUN="$DEF" PUBLIC_FROZEN_PAGES="${PAGES// /,}" \
  npx astro build --outDir "$OUT/site" 2>&1 | grep -vE '^\s*$' | tail -25
cp "$OUT/_freeze/public/frozen/manifest.json" "$OUT/site/frozen/manifest.json"
# Root-absolute asset paths in page markup (e.g. /spawn-tree.js) get the base; links are fixed at click time by Layout.
if [ "$BASE" != "/" ]; then
  find "$OUT/site" -name '*.html' -print0 | xargs -0 perl -pi -e 's#\b(src)="/(?!\Q'"${BASE#/}"'\E)#$1="'"$BASE"'#g'
fi
python3 "$REPO/scripts/freeze/fix_links.py" "$OUT/site" "$BASE" "${PAGES// /,}"
# Media (game clips) stays out of the build unless CKL OKs it: no /artifacts/ files are copied.
du -sh "$OUT/site"
