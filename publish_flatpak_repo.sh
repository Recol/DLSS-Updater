#!/bin/bash
# DLSS Updater - publish the Flatpak OSTree repository
#
# Takes the local `repo/` directory that build_flatpak.sh produced and publishes
# it to https://recol.github.io/dlss-updater-flatpak/, which is the origin remote
# baked into every release bundle by `flatpak build-bundle --repo-url`.
#
# Publishing this is what makes `flatpak update` (and GNOME Software / KDE
# Discover background updates) able to see a new release. Skipping it means the
# bundle attached to the release still installs fine, but nobody who already has
# the app gets offered the upgrade.
#
# Usage (in WSL2 or native Linux, after ./build_flatpak.sh):
#   ./publish_flatpak_repo.sh
#
# The gh-pages branch is REPLACED wholesale on every publish (a single orphan
# commit) rather than appended to. OSTree objects are content-addressed and
# never change, so ordinary commits would accumulate every object ever built
# into the git history and the clone would grow without bound. Only the current
# repo state matters to clients.

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# HTTPS by default so the existing git credential helper / gh auth is used.
# Override with PAGES_REPO=git@github.com:Recol/dlss-updater-flatpak.git for SSH.
PAGES_REPO="${PAGES_REPO:-https://github.com/Recol/dlss-updater-flatpak.git}"
PAGES_BRANCH="gh-pages"
LOCAL_REPO="${LOCAL_REPO:-repo}"
VERSION=$(grep -oP '__version__\s*=\s*"\K[^"]+' dlss_updater/version.py)

# Must match the id build_flatpak.sh exports.
APP_ID="${APP_ID:-io.github.recol.dlss-updater}"

# How many past versions stay in the channel. Deltas are generated between the
# retained commits, so this is also how far back a delta-based update can come
# from; 3 bounds the published size while covering anyone who skipped a release
# or two. Lower it if the size guard below starts complaining.
PRUNE_DEPTH="${PRUNE_DEPTH:-3}"

# Signing, matching build_flatpak.sh - same .flatpak_gpg_key fallback so the two
# scripts can never disagree about whether the channel is signed.
FLATPAK_GPG_KEY="${FLATPAK_GPG_KEY:-}"
if [ -z "$FLATPAK_GPG_KEY" ] && [ -f "$(dirname "$0")/.flatpak_gpg_key" ]; then
    FLATPAK_GPG_KEY=$(tr -d '[:space:]' < "$(dirname "$0")/.flatpak_gpg_key")
fi
GPG_ARGS=()
if [ -n "$FLATPAK_GPG_KEY" ]; then
    GPG_ARGS=(--gpg-sign="$FLATPAK_GPG_KEY")
fi

# Resolved up front: the copy below runs from inside the temp clone, so a
# relative path (or $OLDPWD) would break the moment anything else cd'd.
SOURCE_REPO="$(cd "$(dirname "$0")" && pwd)/${LOCAL_REPO}"

echo -e "${GREEN}Publishing DLSS Updater v${VERSION} Flatpak repository${NC}"

# -----------------------------------------------------------------------------
# Validate the local repo before touching anything remote
# -----------------------------------------------------------------------------
if [ ! -d "$SOURCE_REPO" ]; then
    echo -e "${RED}No '${LOCAL_REPO}' directory found.${NC}"
    echo -e "Run ${YELLOW}./build_flatpak.sh${NC} first - it exports the OSTree repo."
    exit 1
fi

if [ ! -f "$SOURCE_REPO/config" ] || [ ! -d "$SOURCE_REPO/objects" ]; then
    echo -e "${RED}'${LOCAL_REPO}' does not look like an OSTree repository${NC}"
    echo -e "(expected a 'config' file and an 'objects/' directory)."
    exit 1
fi

# A repo with no summary is one clients cannot read. build_flatpak.sh generates
# it, but a hand-assembled repo might not have.
if [ ! -f "$SOURCE_REPO/summary" ]; then
    echo -e "${YELLOW}No summary file - regenerating it now...${NC}"
    flatpak build-update-repo --generate-static-deltas --prune --prune-depth=3 "$SOURCE_REPO"
fi

REPO_SIZE=$(du -sm "$SOURCE_REPO" | cut -f1)
echo -e "Local repository: ${YELLOW}${REPO_SIZE}MB${NC}"

# Checked up front, not at commit time: WSL keeps a git config entirely separate
# from Windows', and a fresh WSL install has no identity at all. Without this the
# run does the whole clone/merge/delta job and only then dies on git's generic
# "empty ident name" error.
if ! git config user.email >/dev/null 2>&1; then
    echo -e "${RED}No git identity configured, so the publish commit would fail.${NC}"
    echo -e "Set one (note WSL's git config is separate from Windows'):"
    echo -e "  ${YELLOW}git config --global user.name  \"Your Name\"${NC}"
    echo -e "  ${YELLOW}git config --global user.email \"you@example.com\"${NC}"
    echo -e "Or export ${YELLOW}GIT_AUTHOR_NAME${NC} / ${YELLOW}GIT_AUTHOR_EMAIL${NC} for this run only."
    exit 1
fi

# GitHub Pages refuses to publish sites over 1GB, and warns past 1GB of repo
# storage. Fail loudly well before that rather than after a long push.
if [ "$REPO_SIZE" -gt 900 ]; then
    echo -e "${RED}Repository is ${REPO_SIZE}MB - too close to the GitHub Pages 1GB limit.${NC}"
    echo -e "Lower --prune-depth in build_flatpak.sh, or move hosting off Pages."
    exit 1
fi
if [ "$REPO_SIZE" -gt 500 ]; then
    echo -e "${YELLOW}Warning: ${REPO_SIZE}MB is over half the GitHub Pages budget.${NC}"
    echo -e "Consider lowering --prune-depth in build_flatpak.sh."
fi

# -----------------------------------------------------------------------------
# Stage the publish in a temp clone
# -----------------------------------------------------------------------------
WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

echo -e "\n${YELLOW}[1/4] Cloning ${PAGES_BRANCH}...${NC}"
git clone --depth 1 --branch "$PAGES_BRANCH" "$PAGES_REPO" "$WORKDIR/pages"

cd "$WORKDIR/pages"

# git does not track empty directories, so cloning gh-pages silently drops the
# empty refs/{heads,mirrors,remotes} and tmp/ that every OSTree repo carries.
# build-commit-from survives that (it creates refs/heads as it writes), but
# build-update-repo enumerates ALL ref directories and dies with:
#   error: Listing refs: opendir(refs/remotes): No such file or directory
#
# This only bites on the merge path: the first-publish branch below seeds the
# channel with `cp -a` from the local repo, which still has the directories. So
# it stayed hidden until the second publish into an existing channel.
mkdir -p refs/heads refs/mirrors refs/remotes tmp


# Preserve the hand-maintained files that live alongside the OSTree repo.
echo -e "\n${YELLOW}[2/4] Preserving landing page and repo definition...${NC}"
mkdir -p "$WORKDIR/keep"
for f in .nojekyll index.html dlss-updater.flatpakrepo; do
    [ -f "$f" ] && cp "$f" "$WORKDIR/keep/$f"
done

# -----------------------------------------------------------------------------
# Merge the new build INTO the published repository
# -----------------------------------------------------------------------------
# The PUBLISHED repo is the source of truth for update history, not the local
# `repo/` directory. That matters because the documented pre-build clean deletes
# `repo/`, so the local repo only ever holds the version just built. Copying it
# over the published one would leave a single-commit channel, and
# --generate-static-deltas would have no previous commit to diff against - every
# update would then be a full ~36MB download instead of just the changed objects.
#
# Committing into a clone of the published repo instead means history accumulates
# server-side and survives a wiped (or lost) WSL working copy.
echo -e "\n${YELLOW}[3/4] Merging build into the published repository...${NC}"

# Publishing an unsigned update over a signed channel is the one failure here
# that breaks working installs: clients that added the remote with gpg-verify=true
# reject the new summary outright and stop updating, with no obvious cause.
# summary.sig is the marker that the live channel is signed.
if [ -f summary.sig ] && [ -z "$FLATPAK_GPG_KEY" ]; then
    echo -e "${RED}The published channel is SIGNED but no signing key is configured.${NC}"
    echo -e "Publishing unsigned would break updates for everyone already verifying"
    echo -e "against the key. Set ${YELLOW}FLATPAK_GPG_KEY${NC} (or restore .flatpak_gpg_key)"
    echo -e "and rebuild so the commit is signed too."
    exit 1
fi

if [ -f config ] && [ -d objects ]; then
    echo "  existing published repo found - committing the new build into it"
    BEFORE=$(find objects -type f 2>/dev/null | wc -l)

    # build-commit-from is flatpak's own build-repo -> public-repo publish step,
    # and preserves the commits already present in the destination.
    flatpak build-commit-from \
        "${GPG_ARGS[@]}" \
        --src-repo="$SOURCE_REPO" \
        .

    AFTER=$(find objects -type f 2>/dev/null | wc -l)
    echo "  objects: ${BEFORE} -> ${AFTER}"
else
    # First publish: nothing to preserve, so seed the channel from the local repo.
    echo "  no published repo yet - seeding the channel from this build"
    find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
    cp -a "$SOURCE_REPO/." .
fi

# Regenerate the summary over the MERGED repo and build deltas between the
# commits now present. Pruning bounds growth (GitHub Pages caps the site at 1GB).
flatpak build-update-repo \
    "${GPG_ARGS[@]}" \
    --generate-static-deltas \
    --prune \
    --prune-depth="$PRUNE_DEPTH" \
    .

# ostree ships alongside flatpak but isn't guaranteed on PATH, so this is
# best-effort reporting only.
if command -v ostree >/dev/null 2>&1; then
    COMMITS=$(ostree --repo=. log "app/${APP_ID}/x86_64/master" 2>/dev/null | grep -c '^commit ' || true)
    [ -n "$COMMITS" ] && echo "  versions now in the channel: ${COMMITS}"
    DELTAS=$(find deltas -mindepth 1 -maxdepth 2 -type d 2>/dev/null | wc -l || true)
    echo "  static deltas present: ${DELTAS:-0}"
fi

cp -a "$WORKDIR/keep/." . 2>/dev/null || true
# Jekyll would mangle a static file tree; .nojekyll disables it.
touch .nojekyll

# -----------------------------------------------------------------------------
# Publish
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[4/4] Publishing...${NC}"

# Orphan commit: one commit, no history, so the clone stays the size of the
# current repo rather than every version ever published.
#
# No "already up to date" short-circuit here: on an unborn branch there is no
# HEAD to diff the index against, and the orphan approach has already discarded
# the previous state. Re-publishing an identical repo is harmless anyway.
git checkout --orphan publish-tmp
git add -A

# Sanity check that what we are about to force-push really is the OSTree repo,
# not an empty tree - this push replaces the live update channel.
if [ ! -f config ] || [ ! -d objects ] || [ ! -f summary ]; then
    echo -e "${RED}Staged tree is missing OSTree essentials - refusing to publish.${NC}"
    exit 1
fi

git commit -q -m "Publish DLSS Updater ${VERSION}

OSTree repository for the io.github.recol.dlss-updater bundle, so installed
copies can find this release through 'flatpak update' and desktop software
centres."

git push -q --force origin "publish-tmp:${PAGES_BRANCH}"

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}Published v${VERSION}${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "URL:  ${YELLOW}https://recol.github.io/dlss-updater-flatpak/${NC}"
echo -e "Size: ${YELLOW}${REPO_SIZE}MB${NC}"
echo -e "\nGitHub Pages takes a minute or two to serve the new content."
echo -e "Verify with (add via the .flatpakrepo so the signing key is picked up -"
echo -e "do NOT pass --no-gpg-verify, since checking the signature is the point):"
echo -e "  ${YELLOW}flatpak remote-add --user --if-not-exists dlss-updater-test \\
    https://recol.github.io/dlss-updater-flatpak/dlss-updater.flatpakrepo${NC}"
echo -e "  ${YELLOW}flatpak remote-info --user dlss-updater-test io.github.recol.dlss-updater${NC}"
echo -e "  ${YELLOW}flatpak remote-delete --user --force dlss-updater-test${NC}"
