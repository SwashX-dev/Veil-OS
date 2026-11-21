#!/bin/bash
set -eu
workspace="$(mktemp -d)"
trap 'rm -rf "$workspace"' EXIT
mkdir "$workspace/keys" "$workspace/repo"
chmod 700 "$workspace/keys"
cd "$workspace/repo"
make_package() {
    local name="$1" version="$2" stage="$workspace/staging-$1-$2"
    mkdir -p "$stage/DEBIAN" "$stage/usr/share/doc/veil-vm-test-$name"
    cat > "$stage/DEBIAN/control" <<CONTROL
Package: veil-vm-test-$name
Version: $version
Architecture: all
Maintainer: Veil Test <test@example.invalid>
Description: Veil offline persistence regression fixture
CONTROL
    if [ "$name" = root ]; then printf 'Depends: veil-vm-test-dep\n' >> "$stage/DEBIAN/control"; fi
    if [ "$name" = interrupted ]; then
        for hook in postinst prerm; do
            cat > "$stage/DEBIAN/$hook" <<SCRIPT
#!/bin/sh
if [ -e /run/veil-apt-hold-$hook ]; then
    touch /run/veil-apt-entered-$hook
    exec sleep 300
fi
SCRIPT
            chmod +x "$stage/DEBIAN/$hook"
        done
    fi
    echo "$name" > "$stage/usr/share/doc/veil-vm-test-$name/marker"
    dpkg-deb --build --root-owner-group "$stage" "veil-vm-test-$name.deb" >&2
}

sign_repository() {
    apt-ftparchive packages . > Packages
    gzip -kn Packages
    apt-ftparchive release . > Release
    gpg --homedir "$workspace/keys" --batch --clearsign -o InRelease Release
}

gpg --homedir "$workspace/keys" --batch --pinentry-mode loopback --passphrase '' --quick-gen-key 'Veil QEMU fixture <test@example.invalid>' ed25519 sign 0
for name in root dep extra interrupted; do
    make_package "$name" 1.0
done
sign_repository

mkdir upgrade
cp ./*.deb upgrade/
(
    cd upgrade
    make_package root 2.0
    sign_repository
)
gpg --homedir "$workspace/keys" --batch --export > fixture.gpg
tar -czf - .
