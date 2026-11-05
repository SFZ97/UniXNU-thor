#!/usr/bin/env bash
# Thor (SM8475) packaging that builds the Pong Darwin 24 kernel aggregate instead of
# the shared BCM2837 kernel step.
#
# Why this exists: `unixnu release` runs the shared kernel step, which hard-codes
# scripts/build-xnu-4k-baseline.sh and its MACHINE_CONFIGS=BCM2837 (a Raspberry Pi
# machine config). This board's XNU patches only provide config/MASTER.arm64.pong
# (MACHINE_CONFIGS=PONG), so the shared step dies at `make installhdrs` with
# "Unsupported CURRENT_MACHINE_CONFIG BCM2837".
#
# The correct builder is the PongPlatform24 component recipe
# (kits/PongPlatform24/build-with-runtime.py -> build-kernel.py -> build-xnu-ipados18.sh,
# MACHINE_CONFIGS=PONG). `release` only ever runs that recipe inside build_archives(),
# which is gated behind XNU_BUILD_ARCHIVES and, more fundamentally, runs *after* the
# hard-coded baseline.sh bootstrap has already failed. So this script reproduces the
# fixed `release` steps (deps, preflight, patches, build, pack) but swaps the kernel
# step for the component recipe, then lets `unixnu` run prelink/package/firmware/
# payload/bootimg with the kernel step omitted.
#
# This mirrors the "Folding that into the shared step is open" note in board.toml
# [kernel] and porting.toml's packaging area. It is expected to be replaced once the
# shared kernel step learns to select PONG for this board.
set -euo pipefail

DEVICE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

# The UniXNU checkout: board.toml [package] unixnu is "../UniXNU" relative to this repo.
UNIXNU="$DEVICE/../UniXNU"
if [ ! -f "$UNIXNU/unixnu" ]; then
  echo "UniXNU checkout not found at $UNIXNU" >&2
  exit 1
fi

OS_PROFILE="${OS_PROFILE:-ipados-18.7.10-22H374}"
# The defconfig file name (configs/<name>_defconfig). The CI repoints
# [package.defconfig] to the ci defconfig, which drops the Wi-Fi/backlight/Adreno
# stages the public repository does not carry.
CONFIG_NAME="${PONG_CONFIG_NAME:-thor_ipados18_ci_defconfig}"
UEFI="${UNIXNU_UEFI:-$DEVICE/../uefi-pong}"
JOBS="${JOBS:-3}"

# `unixnu` writes its output under out/<device>/<os>/<defconfig-without-suffix>/.
WORK="$UNIXNU/out/thor/$OS_PROFILE/${CONFIG_NAME%_defconfig}"
TREE="$WORK/tree"

# Run `unixnu` with the device and OS profile this board resolves to.
u() { python3 "$UNIXNU/unixnu" "$@" -D "$DEVICE" --os "$OS_PROFILE"; }

echo "=== step deps (fetch sources; IPSW components as the release step would) ==="
u fetch
# The release deps step also fetches the IPSW members [ipsw] fetch names (KernelCache,
# DeviceTree, StaticTrustCache, Cryptex1 ...). The payload and pack steps read some of
# them even when the kernel is staged native (--skip-prelink).
u ipsw fetch

# prepare-ipados18-storage.py reads the original 22H374 kernel cache from
# apple/ipados18-22H374/components/kernelcache.release.ipad7c.decompressed/,
# but `u ipsw fetch` extracts it to out/ipsw/22H374/kernelcache.decompressed.
# Bridge the two layouts so the storage ABI derivation can find it.
APPLE="$DEVICE/apple/ipados18-22H374/components/kernelcache.release.ipad7c.decompressed"
# `u ipsw fetch` extracts the kernel cache under out/ipsw/<build-number>/,
# e.g. out/ipsw/22H374/kernelcache.decompressed — not the full OS profile name.
IPSW_KERNEL=$(find "$UNIXNU/out/ipsw" -name kernelcache.decompressed -type f 2>/dev/null | head -1)
if [ ! -e "$APPLE/kernelcache.release.ipad7c.decompressed" ] && [ -n "$IPSW_KERNEL" ]; then
  mkdir -p "$APPLE"
  ln -sf "$IPSW_KERNEL" "$APPLE/kernelcache.release.ipad7c.decompressed"
  echo "linked apple kernel cache -> $IPSW_KERNEL"
fi

echo "=== step preflight (write the resolved configuration) ==="
u defconfig "$CONFIG_NAME"

echo "=== step patches (assemble the build tree; applies the patch series) ==="
u tree

echo "=== step build: kernel (Pong component recipe, MACHINE_CONFIGS=PONG) ==="
# The component catalog's build line for PongPlatform24 is:
#   {component}/build-with-runtime.py --artifact-dir {out}/kernel --tree {tree}
# {component} resolves to this repository's kits/PongPlatform24. build-with-runtime.py
# builds in a source-only overlay and stages the aggregate at
# $TREE/build/xnu/ios-obj/DEVELOPMENT_ARM64_PONG/mach.development.pong, which is exactly
# the path [kernel.prelink] kernel names.
python3 "$DEVICE/kits/PongPlatform24/build-with-runtime.py" \
  --artifact-dir "$WORK/kernel" \
  --tree "$TREE" \
  --jobs "$JOBS"

echo "=== step build: prelink (stage the native aggregate as kernel.macho) ==="
# --skip-prelink makes prelink stage the built native kernel unchanged instead of
# prelinking the original 22H374 kernelcache. This is the bootable image for bring-up.
u build prelink --skip-prelink --uefi "$UEFI"

echo "=== step build: package, firmware and payload (kernel step omitted) ==="
# [firmware] packages names {tree}/drivers/DarwinBoot24, but schema_version 2's
# tree step keeps only the components [components] lists, so drivers/ has no
# entries. Stage the UEFI package as a real directory: the tree step clears
# only symlinks, so it survives the rebuild `u build` runs before the firmware.
if [ ! -d "$TREE/drivers/DarwinBoot24" ]; then
  mkdir -p "$TREE/drivers"
  cp -R "$DEVICE/drivers/DarwinBoot24" "$TREE/drivers/DarwinBoot24"
  echo "staged firmware package DarwinBoot24 -> $TREE/drivers/DarwinBoot24"
fi
u build package firmware payload --uefi "$UEFI" -j "$JOBS"

echo "=== step pack: boot image ==="
# The device tree is written by the firmware preflight step
# (scripts/build-pong-hardware-dtb.py) into the build tree; [bootimg] dtb names the
# same path. bootimg takes the firmware volume from firmware.fd_path().
u bootimg --uefi "$UEFI" --dtb "$TREE/build/pong/pong.dtb"

echo "=== packaging complete; products under $WORK ==="
find "$WORK" \( -path '*/boot/*' -o -path '*/payload/*' -o -path '*/kernel/*' \
  -o -name 'package.manifest.json' \) -type f 2>/dev/null | sort
