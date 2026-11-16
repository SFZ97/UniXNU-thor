#!/usr/bin/env python3
"""Add the verified Thor Recovery FDT cellular topology to a copy of a boot ADT."""

import argparse
from pathlib import Path
import struct
import subprocess
import tempfile
import zlib

from pong_adt_contract import parse_adt


PROFILE = 'thor-cellular-profile'
MAGIC = int.from_bytes(b'THCL', 'little')


def cellular_profile(tree, source_crc):
    def get(node, prop, kind='x'):
        result = subprocess.run(
            ['fdtget', '-t', kind, str(tree), node, prop], capture_output=True, text=True
        )
        if result.returncode:
            raise ValueError(f'{node}:{prop}: {result.stderr.strip()}')
        return result.stdout.strip()

    def cells(node, prop):
        return tuple(int(value, 16) for value in get(node, prop).split())

    def require(node, prop, expected):
        actual = cells(node, prop) if isinstance(expected, tuple) else get(node, prop, 's')
        if actual != expected:
            raise ValueError(f'{node}:{prop}: expected {expected!r}, got {actual!r}')
        return actual

    require('/', 'model', 'thor based on Qualcomm Technologies, Inc SM8475')
    if 'qcom,cape' not in get('/', 'compatible', 's').split():
        raise ValueError('Expected a Thor Cape FDT')

    reserved = '/reserved-memory'
    modem = '/soc/remoteproc-mss@04080000'
    smp2p = '/soc/qcom,smp2p-modem'
    ipcc = '/soc/qcom,ipcc@ed18000'
    ipa = '/soc/qcom,ipa@3e00000'
    glink = modem + '/glink-edge'
    ranges = (
        ('smem_region@80900000', (0, 0x80900000, 0, 0x200000)),
        ('ipa_fw_region@8b900000', (0, 0x8B900000, 0, 0x10000)),
        ('ipa_gsi_region@8b910000', (0, 0x8B910000, 0, 0xA000)),
        ('mpss_region@8bc00000', (0, 0x8BC00000, 0, 0x13200000)),
    )
    region_words = []
    for name, expected in ranges:
        node = f'{reserved}/{name}'
        _, start, _, size = require(node, 'reg', expected)
        require(node, 'no-map', ())
        region_words.extend((start, size))

    require(modem, 'compatible', 'qcom,cape-modem-pas')
    if get(modem, 'status', 's') not in ('ok', 'okay'):
        raise ValueError('Thor modem PAS is disabled in the source FDT')
    modem_base, modem_size = require(modem, 'reg', (0x04080000, 0x10000))
    mpss_id, = cells(f'{reserved}/{ranges[3][0]}', 'phandle')
    cma_id, = cells(f'{reserved}/linux,cma', 'phandle')
    require(modem, 'memory-region', (mpss_id, cma_id))
    require(glink, 'transport', 'smem')
    require(glink, 'qcom,glink-label', 'mpss')
    require(glink + '/qcom,modem_qrtr', 'qcom,glink-channels', 'IPCRTR')
    remote_pid, = require(glink, 'qcom,remote-pid', (1,))
    require(smp2p, 'qcom,local-pid', (0,))
    require(smp2p, 'qcom,remote-pid', (remote_pid,))
    smp_tx, smp_rx = require(smp2p, 'qcom,smem', (435, 428))
    ipcc_base, _ = require(ipcc, 'reg', (0x0ED18000, 0x1000))
    ipcc_id, = cells(ipcc, 'phandle')
    require(glink, 'mboxes', (ipcc_id, 2, 0))
    rmtfs = '/soc/qcom,rmtfs_sharedmem@0'
    require(rmtfs, 'qcom,client-id', (1,))
    _, rmtfs_size = require(rmtfs, 'reg', (0, 0x280000))

    require(ipa, 'reg-names', 'ipa-base gsi-base')
    ipa_base, _, gsi_base, _ = require(ipa, 'reg', (0x03E00000, 0x84000, 0x03E04000, 0xFC000))
    ipa_offset, = require(ipa, 'qcom,ipa-cfg-offset', (0x140000,))
    version_id, = require(ipa, 'qcom,ipa-hw-ver', (0x16,))
    ipa_pas, = require(ipa, 'pas-ids', (0x0F,))
    require(ipa, 'firmware-names', 'ipa_fws')
    gsi_mem_id, = cells(f'{reserved}/{ranges[2][0]}', 'phandle')
    require(ipa, 'memory-regions', (gsi_mem_id,))
    require(ipa, 'interrupt-names', 'ipa-irq gsi-irq')
    _, ipa_spi, _, _, gsi_spi, _ = require(ipa, 'interrupts', (0, 654, 4, 0, 432, 4))
    ap_iommu, ap_sid, ap_flags = cells(ipa + '/ipa_smmu_ap', 'iommus')
    uc_iommu, uc_sid, uc_flags = cells(ipa + '/ipa_smmu_uc', 'iommus')
    if ((ap_sid, uc_sid, ap_flags, uc_flags) != (0x5C0, 0x5C2, 0, 0)
            or not ap_iommu or ap_iommu != uc_iommu):
        raise ValueError('Unexpected Thor IPA SMMU configuration')

    return struct.pack(
        '<28I', MAGIC, 1, source_crc, len(ranges), *region_words,
        modem_base, modem_size, ipcc_base, ipa_base, ipa_base + ipa_offset, gsi_base,
        version_id, ipa_pas, ap_sid, uc_sid, remote_pid, smp_tx, smp_rx,
        rmtfs_size, ipa_spi, gsi_spi,
    )


def prepare(adt, profile):
    nodes = parse_adt(adt)
    chosen = nodes.get('/chosen')
    if chosen is None:
        raise ValueError('ADT is missing /chosen')
    if PROFILE in chosen:
        raise ValueError(f'{PROFILE} already exists in ADT')

    # parse_adt visits nodes in their serialized order. Insert only the new
    # property, keeping all existing bytes, including padding, intact.
    offset = 0
    for path, properties in nodes.items():
        end = offset + 8 + sum(36 + ((len(value) + 3) & ~3) for value in properties.values())
        if path == '/chosen':
            break
        offset = end
    prop = struct.pack('<32sI', PROFILE.encode('ascii'), len(profile)) + profile
    packed = (adt[:offset] + struct.pack('<I', len(chosen) + 1)
              + adt[offset + 4:end] + prop + adt[end:])
    chosen[PROFILE] = profile
    if parse_adt(packed) != nodes:
        raise ValueError('Cellular profile ADT round-trip failed')
    return packed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adt', type=Path, required=True, help='input boot ADT')
    parser.add_argument('--fdt', type=Path, required=True, help='local stock Thor Recovery DTB')
    parser.add_argument('--output', type=Path, required=True, help='new boot ADT path')
    args = parser.parse_args()
    try:
        source = args.fdt.read_bytes()
        # All fdtget reads and the source CRC must describe the same snapshot.
        with tempfile.TemporaryDirectory(prefix='thor-cellular-') as directory:
            tree = Path(directory) / 'source.dtb'
            tree.write_bytes(source)
            profile = cellular_profile(tree, zlib.crc32(source))
        packed = prepare(args.adt.read_bytes(), profile)
        with args.output.open('xb') as output:
            output.write(packed)
    except FileNotFoundError as error:
        if error.filename == 'fdtget':
            parser.exit(1, 'fdtget from device-tree-compiler is required\n')
        parser.exit(1, f'{error}\n')
    except (OSError, ValueError) as error:
        parser.exit(1, f'{error}\n')
    print(f'Verified {PROFILE} ({len(profile)} bytes) from Thor Recovery FDT')


if __name__ == '__main__':
    main()
