#!/usr/bin/env python3
"""Copy a bare APFS container into a local, single-partition GPT image.

The output includes a protective MBR and both GPT headers and entry tables.
Provide a new output path. The input partition payload is copied byte for byte.
"""
import argparse
import struct
import uuid
import zlib
from pathlib import Path

LBA = 512
PART_OFFSET_LBA = 2048          # 1 MiB alignment for the partition start
APFS_TYPE = uuid.UUID('7c3457ef-0000-11aa-aa11-00306543ecac')
ENTRY_COUNT = 128
ENTRY_SIZE = 128
ENTRIES_LBAS = ENTRY_COUNT * ENTRY_SIZE // LBA   # 32 LBAs


def gpt_header(disk_last_lba, current_lba, backup_lba, entries_lba,
               disk_guid, entries_crc):
    h = bytearray(92)
    h[0:8] = b'EFI PART'
    struct.pack_into('<I', h, 8, 0x00010000)            # 0x08 revision
    struct.pack_into('<I', h, 12, 92)                   # 0x0C header size
    # 0x10 header CRC = 0 (placeholder), 0x14 reserved = 0
    struct.pack_into('<Q', h, 24, current_lba)          # 0x18 current LBA
    struct.pack_into('<Q', h, 32, backup_lba)           # 0x20 backup LBA
    struct.pack_into('<Q', h, 40, 34)                   # 0x28 first usable
    struct.pack_into('<Q', h, 48, disk_last_lba - ENTRIES_LBAS - 1)  # 0x30 last usable
    h[56:72] = disk_guid.bytes_le                       # 0x38 disk GUID
    struct.pack_into('<Q', h, 72, entries_lba)          # 0x48 entries LBA
    struct.pack_into('<I', h, 80, ENTRY_COUNT)          # 0x50 entry count
    struct.pack_into('<I', h, 84, ENTRY_SIZE)           # 0x54 entry size
    struct.pack_into('<I', h, 88, entries_crc)          # 0x58 entries CRC
    struct.pack_into('<I', h, 16, zlib.crc32(bytes(h)) & 0xFFFFFFFF)
    return h


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--part-guid', default=None, help='partition GUID (hex/uuid string)')
    a = p.parse_args()

    src = a.input
    size = src.stat().st_size
    if not src.is_file() or size <= 0 or size % LBA:
        p.error('input must be a nonempty 512-byte-aligned file')
    part_lba_count = size // LBA
    first = PART_OFFSET_LBA
    last = first + part_lba_count - 1
    disk_last_lba = last + ENTRIES_LBAS + 1             # backup entries + header
    total = (disk_last_lba + 1) * LBA

    part_guid = uuid.UUID(a.part_guid) if a.part_guid else uuid.uuid4()
    disk_guid = uuid.uuid5(uuid.NAMESPACE_OID, str(part_guid))

    # protective MBR: one entry of type 0xEE spanning the disk
    mbr = bytearray(LBA)
    mbr[510:512] = b'\x55\xaa'
    struct.pack_into('<B3sB3sII', mbr, 446, 0, b'\x00\x02\x00', 0xEE, b'\xff' * 3,
                     1, min(disk_last_lba, 0xFFFFFFFF))

    # partition entry
    entry = bytearray(ENTRY_SIZE)
    entry[0:16] = APFS_TYPE.bytes_le
    entry[16:32] = part_guid.bytes_le
    struct.pack_into('<QQQ', entry, 32, first, last, 0)
    partition_name = 'UniXNU OS/Data'.encode('utf-16-le')
    entry[56:56 + len(partition_name)] = partition_name
    entries = bytearray(ENTRY_COUNT * ENTRY_SIZE)
    entries[0:ENTRY_SIZE] = entry
    entries_crc = zlib.crc32(bytes(entries)) & 0xFFFFFFFF

    primary = gpt_header(disk_last_lba, 1, disk_last_lba, 2, disk_guid, entries_crc)
    backup = gpt_header(disk_last_lba, disk_last_lba, 1,
                        disk_last_lba - ENTRIES_LBAS, disk_guid, entries_crc)

    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open('xb') as out:
        out.write(mbr)
        out.write(bytes(primary))
        out.seek(2 * LBA)
        out.write(bytes(entries))
        out.seek(first * LBA)
        copied = 0
        with src.open('rb') as f:
            while chunk := f.read(8 << 20):
                out.write(chunk)
                copied += len(chunk)
        if copied != size:
            raise OSError('input file size changed while copying')
        out.truncate(total)
        out.seek((disk_last_lba - ENTRIES_LBAS) * LBA)
        out.write(bytes(entries))
        out.seek(disk_last_lba * LBA)
        out.write(bytes(backup))

    print(f'{a.output}: container {size} at lba {first}, disk {total} bytes, '
          f'part-guid {part_guid}')


if __name__ == '__main__':
    main()
