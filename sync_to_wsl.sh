#!/bin/bash
# Sync the Windows working tree into the WSL build checkout.
#
# The Flatpak signing artefacts (.flatpak_gpg_key, repo-key.gpg) exist ONLY in
# WSL and are gitignored, so they must be excluded - otherwise a sync run with
# --delete removes them and the next build silently produces an unsigned repo,
# which breaks `flatpak update` for everyone verifying against the key.
rsync -avz --progress \
  --exclude=.venv/ --exclude=venv/ --exclude=__pycache__/ --exclude="*.pyc" \
  --exclude=.git/ --exclude=build/ --exclude=dist/ --exclude="*.egg-info/" \
  --exclude=.mypy_cache/ --exclude=.pytest_cache/ --exclude=.ruff_cache/ \
  --exclude="*.log" --exclude=node_modules/ --exclude=repo/ --exclude=build-dir/ \
  --exclude="*.flatpak" --exclude=.flet/ --exclude=flet_client/ \
  --exclude=.flatpak-builder/ \
  --exclude=.flatpak_gpg_key --exclude="repo-key.gpg*" \
  --exclude=.build_cache/ \
  /mnt/c/Github/DLSS-Updater/ ~/DLSS-Updater/
