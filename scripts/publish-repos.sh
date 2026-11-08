#!/bin/bash
# Publish signed APT, RPM and Arch repositories to the gh-pages branch in ONE
# commit (same layout as collyn/skey). Inputs in the current directory:
#   *.deb, *.rpm, *.pkg.tar.zst (+ .sig) and the Arch db from the arch-repo job.
# Needs: gpg with APT_GPG_KEY_EMAIL's secret key, dpkg-scanpackages,
# apt-ftparchive, createrepo_c, rpmsign; GITHUB_TOKEN in Actions.
set -euo pipefail
shopt -s nullglob

ORIG_DIR="$(pwd)"
GPG_KEY_EMAIL="${APT_GPG_KEY_EMAIL:?APT_GPG_KEY_EMAIL is required}"
PACKAGE=anvildroid-controller
KEEP=3

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    REPO_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
    REPO_NAME="${GITHUB_REPOSITORY##*/}"
    REPO_OWNER="${GITHUB_REPOSITORY_OWNER}"
else
    REPO_URL="$(git remote get-url origin)"
    REPO_NAME="anvildroid"
    REPO_OWNER="collyn"
fi
PAGES_URL="https://${REPO_OWNER}.github.io/${REPO_NAME}"

if git ls-remote --exit-code --heads "$REPO_URL" gh-pages >/dev/null 2>&1; then
    git clone --depth=1 -b gh-pages "$REPO_URL" "$WORKDIR"
else
    git clone --depth=1 "$REPO_URL" "$WORKDIR"
    (cd "$WORKDIR" && git checkout -q --orphan gh-pages && git rm -rfq --ignore-unmatch .)
fi
cd "$WORKDIR"

# Keep the newest $KEEP-1 versions; the release being published makes $KEEP.
prune() {
    local dir="$1" keep=$((KEEP - 1)) versions
    [ -d "$dir" ] || return 0
    versions=$(find "$dir" -maxdepth 1 -type f -name "${PACKAGE}[-_][0-9]*" | \
        sed -nE "s#^.*/${PACKAGE}[-_]([0-9][0-9A-Za-z.~+]*)[-_.].*#\\1#p" | sort -Vu | tail -n "$keep")
    find "$dir" -maxdepth 1 -type f -name "${PACKAGE}[-_][0-9]*" | while read -r file; do
        version=$(basename "$file" | sed -nE "s#^${PACKAGE}[-_]([0-9][0-9A-Za-z.~+]*)[-_.].*#\\1#p")
        if [ -n "$version" ] && ! grep -qxF "$version" <<<"$versions"; then
            echo "→ Prune $(basename "$file")"
            rm -f "$file"
        fi
    done
}
prune pool/main/a/$PACKAGE
prune rpm
prune arch/x86_64

# ── APT ──────────────────────────────────────────────────────────────────
mkdir -p pool/main/a/$PACKAGE dists/stable/main/binary-amd64
debs=("$ORIG_DIR"/${PACKAGE}_*_amd64.deb)
[ ${#debs[@]} -gt 0 ] || { echo "No .deb to publish" >&2; exit 1; }
cp "${debs[@]}" pool/main/a/$PACKAGE/
dpkg-scanpackages --multiversion --arch amd64 pool/ > dists/stable/main/binary-amd64/Packages
gzip -9c dists/stable/main/binary-amd64/Packages > dists/stable/main/binary-amd64/Packages.gz
cat > "$WORKDIR/.apt-release.conf" <<'EOF'
APT::FTPArchive::Release {
    Origin "AnvilDroid";
    Label "AnvilDroid";
    Suite "stable";
    Codename "stable";
    Architectures "amd64";
    Components "main";
    Description "AnvilDroid — Waydroid manager for Linux";
};
EOF
apt-ftparchive -c="$WORKDIR/.apt-release.conf" release dists/stable > dists/stable/Release
rm -f "$WORKDIR/.apt-release.conf"
gpg --batch --yes --local-user "$GPG_KEY_EMAIL" --detach-sign --armor -o dists/stable/Release.gpg dists/stable/Release
gpg --batch --yes --local-user "$GPG_KEY_EMAIL" --clearsign -o dists/stable/InRelease dists/stable/Release

# ── RPM (one repository: Fedora and openSUSE use the same x86_64 build) ──
mkdir -p rpm
for rpm in "$ORIG_DIR"/${PACKAGE}-*.x86_64.rpm; do
    cp "$rpm" rpm/
    rpmsign --define "_gpg_name $GPG_KEY_EMAIL" --addsign "rpm/$(basename "$rpm")"
done
if compgen -G "rpm/*.rpm" >/dev/null; then
    createrepo_c --update rpm >/dev/null
    gpg --batch --yes --local-user "$GPG_KEY_EMAIL" --detach-sign --armor -o rpm/repodata/repomd.xml.asc rpm/repodata/repomd.xml
fi

# ── Arch (package, signature and signed db come from the arch-repo job) ──
mkdir -p arch/x86_64
for file in "$ORIG_DIR"/${PACKAGE}-*-x86_64.pkg.tar.zst "$ORIG_DIR"/${PACKAGE}-*-x86_64.pkg.tar.zst.sig \
            "$ORIG_DIR"/anvildroid.db* "$ORIG_DIR"/anvildroid.files*; do
    # pacman fetches anvildroid.db; repo-add made it a symlink. Publish files.
    if [ -e "$file" ]; then cp -L "$file" arch/x86_64/; fi
done

# ── Keys and installers ──────────────────────────────────────────────────
gpg --export --armor "$GPG_KEY_EMAIL" > key.asc
touch .nojekyll

cat > install.sh <<EOF
#!/bin/bash
# AnvilDroid APT repository (Debian/Ubuntu/Mint).
set -e
sudo mkdir -p /etc/apt/keyrings
curl -fsSL ${PAGES_URL}/key.asc | sudo gpg --dearmor --yes -o /etc/apt/keyrings/anvildroid.gpg
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/anvildroid.gpg] ${PAGES_URL} stable main" | sudo tee /etc/apt/sources.list.d/anvildroid.list >/dev/null
sudo apt-get update
sudo apt-get install -y ${PACKAGE}
echo "✓ AnvilDroid installed; updates arrive with apt upgrade."
EOF
cat > install-fedora.sh <<EOF
#!/bin/bash
# AnvilDroid RPM repository (Fedora).
set -e
sudo rpm --import ${PAGES_URL}/key.asc
sudo tee /etc/yum.repos.d/anvildroid.repo >/dev/null <<'REPO'
[anvildroid]
name=AnvilDroid
baseurl=${PAGES_URL}/rpm/
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=${PAGES_URL}/key.asc
REPO
sudo dnf install -y ${PACKAGE}
echo "✓ AnvilDroid installed; updates arrive with dnf upgrade."
EOF
cat > install-opensuse.sh <<EOF
#!/bin/bash
# AnvilDroid RPM repository (openSUSE).
set -e
sudo rpm --import ${PAGES_URL}/key.asc
sudo zypper addrepo --refresh --check --gpgcheck ${PAGES_URL}/rpm/ anvildroid
sudo zypper --non-interactive install ${PACKAGE}
EOF
cat > install-arch.sh <<EOF
#!/bin/bash
# AnvilDroid pacman repository (Arch Linux).
set -e
curl -fsSL ${PAGES_URL}/key.asc -o /tmp/anvildroid-key.asc
sudo pacman-key --add /tmp/anvildroid-key.asc
sudo pacman-key --lsign-key ${GPG_KEY_EMAIL}
rm -f /tmp/anvildroid-key.asc
if ! grep -q '^\[anvildroid\]' /etc/pacman.conf; then
    printf '\n[anvildroid]\nSigLevel = Required DatabaseOptional\nServer = ${PAGES_URL}/arch/x86_64\n' | sudo tee -a /etc/pacman.conf >/dev/null
fi
sudo pacman -Sy --noconfirm ${PACKAGE}
echo "✓ AnvilDroid installed; updates arrive with pacman -Syu."
EOF
chmod +x install*.sh

LATEST=$(ls pool/main/a/$PACKAGE/*.deb | sed -nE "s#.*/${PACKAGE}_([^_]+)_amd64.deb#\\1#p" | sort -V | tail -1)
cat > index.html <<EOF
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AnvilDroid packages</title>
<style>
:root{--bg:#f6f8fb;--fg:#0f1b2d;--muted:#44556c;--card:#fff;--border:#d6dfea;--accent:#1a73d9}
@media (prefers-color-scheme:dark){:root{--bg:#0b1220;--fg:#e6edf6;--muted:#9fb0c4;--card:#121c2e;--border:#24344a;--accent:#4ca7ff}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:40px 16px}
h1{margin:0 0 4px}p{color:var(--muted)}
section{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px 20px;margin:16px 0}
code{display:block;overflow-x:auto;padding:10px 12px;border-radius:8px;background:var(--bg);border:1px solid var(--border);font-size:13px}
a{color:var(--accent)}
</style></head><body><main>
<h1>AnvilDroid</h1>
<p>Waydroid manager for Linux · latest ${LATEST} · <a href="https://github.com/${REPO_OWNER}/${REPO_NAME}/releases/latest">Release notes &amp; AppImage</a></p>
<section><h2>Debian, Ubuntu, Linux Mint</h2><code>curl -fsSL ${PAGES_URL}/install.sh | sudo bash</code></section>
<section><h2>Fedora</h2><code>curl -fsSL ${PAGES_URL}/install-fedora.sh | sudo bash</code></section>
<section><h2>openSUSE</h2><code>curl -fsSL ${PAGES_URL}/install-opensuse.sh | sudo bash</code></section>
<section><h2>Arch Linux</h2><code>curl -fsSL ${PAGES_URL}/install-arch.sh | sudo bash</code></section>
<p>Packages and repository metadata are signed with <a href="key.asc">this key</a>. Installed copies also update from Settings → Updates.</p>
</main></body></html>
EOF

git add -A
git -c user.email="github-actions[bot]@users.noreply.github.com" -c user.name="github-actions[bot]" \
    commit -qm "Publish AnvilDroid ${LATEST} packages" || { echo "No changes to publish."; exit 0; }
git push -q origin gh-pages
echo "✓ Published to ${PAGES_URL}"
