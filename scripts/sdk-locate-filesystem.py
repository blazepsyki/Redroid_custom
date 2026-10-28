#!/usr/bin/env python3
"""Identify an Android emulator system image filesystem, including GPT-wrapped ext4.

Read-only inspection. Outputs: filesystem_type<TAB>byte_offset<TAB>byte_length.
No assumptions about a fixed partition offset or physical device names.
"""
import os
import struct
import sys
import zlib

SECTOR = 512


def fail(message):
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def filesystem_type(stream, position, length):
    if length < 2048:
        return None
    stream.seek(position)
    prefix = stream.read(4096)
    # ext2/3/4 superblock: 1024 bytes from partition start; magic at +0x38.
    if prefix[1080:1082] == b"\x53\xef":
        # The API35 image should be ext4; probe the incompat feature bits
        # to avoid mounting older ext2/3 layouts as an unknown format.
        return "ext4"
    # EROFS superblock magic at partition offset + 1024.
    if prefix[1024:1028] == b"\xe2\xe1\xf5\xe0":
        return "erofs"
    if prefix[:4] == b"hsqs":
        return "squashfs"
    return None


def parse_gpt(stream, disk_size):
    stream.seek(SECTOR)
    header_sector = stream.read(SECTOR)
    if header_sector[:8] != b"EFI PART":
        fail("GPT 서명이 없습니다. GPT 헤더가 손상되었거나 지원하지 않는 이미지입니다.")
    header_size = struct.unpack_from("<I", header_sector, 12)[0]
    if not 92 <= header_size <= SECTOR:
        fail("GPT 헤더 크기가 올바르지 않습니다.")
    header_crc = struct.unpack_from("<I", header_sector, 16)[0]
    header_copy = bytearray(header_sector[:header_size])
    header_copy[16:20] = bytes(4)
    if zlib.crc32(header_copy) != header_crc:
        fail("GPT 헤더 CRC가 일치하지 않습니다. 원본 이미지를 확인하세요.")
    table_lba, count, entry_size, table_crc = struct.unpack_from("<QIII", header_sector, 72)
    if count <= 0 or count > 4096 or entry_size < 128 or entry_size > 4096:
        fail("GPT 파티션 목록 크기가 비정상적입니다.")
    table_offset = table_lba * SECTOR
    table_bytes = count * entry_size
    if table_offset + table_bytes > disk_size:
        fail("GPT 파티션 목록이 파일 범위를 벗어납니다.")
    stream.seek(table_offset)
    table = stream.read(table_bytes)
    if zlib.crc32(table) != table_crc:
        fail("GPT 파티션 목록 CRC가 일치하지 않습니다. 원본 이미지를 확인하세요.")

    matches = []
    super_matches = []
    for i in range(count):
        entry = table[i * entry_size:(i + 1) * entry_size]
        if not any(entry[:16]):
            continue
        first, last = struct.unpack_from("<QQ", entry, 32)
        if first <= 0 or last < first:
            fail(f"GPT 파티션 {i + 1}의 시작/끝 위치가 비정상적입니다.")
        offset = first * SECTOR
        length = (last - first + 1) * SECTOR
        if offset + length > disk_size:
            fail(f"GPT 파티션 {i + 1}이 이미지 범위를 벗어납니다.")
        name = entry[56:128].decode("utf-16le", errors="replace").split("\x00", 1)[0]
        kind = filesystem_type(stream, offset, length)
        if name == "super":
            # An Android logical partition container is not an ordinary filesystem.
            # Validate that LP geometry is at +4096 or +8192 before selecting it.
            stream.seek(offset + 4096)
            primary = stream.read(4)
            stream.seek(offset + 8192)
            backup = stream.read(4)
            if primary == b"gDla" or backup == b"gDla":
                super_matches.append(("super", offset, length))
            else:
                fail(f"GPT 파티션 {i+1}은 super로 표시되었으나 LP geometry magic이 없습니다.")
        print(f"[INFO] GPT 파티션 {i+1} ({name}): offset={offset} bytes, size={length} bytes, fs={kind or ('super' if name == 'super' else 'unrecognized')}", file=sys.stderr)
        if kind:
            matches.append((kind, offset, length))
    if len(super_matches) == 1 and not matches:
        return super_matches[0]
    if len(matches) == 1 and not super_matches:
        return matches[0]
    fail(f"파일시스템 후보 {len(matches)}개, super 후보 {len(super_matches)}개입니다. 자동 선택을 중단합니다.")


def main():
    if len(sys.argv) != 2:
        fail("사용법: locate-filesystem.py <system.img>")
    image_path = sys.argv[1]
    disk_size = os.path.getsize(image_path)
    with open(image_path, "rb") as stream:
        kind = filesystem_type(stream, 0, disk_size)
        if kind:
            result = kind, 0, disk_size
        else:
            stream.seek(0)
            prefix = stream.read(512)
            # GPT disks are commonly reported by `file` as protective DOS/MBR (0xee).
            if prefix[510:512] != b"\x55\xaa" or prefix[450] != 0xEE:
                fail("지원하지 않는 원본 형식입니다. file/fdisk 출력으로 진단해 주세요.")
            result = parse_gpt(stream, disk_size)
    print("\t".join(map(str, result)))


if __name__ == "__main__":
    main()
