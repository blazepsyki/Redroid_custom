#!/usr/bin/env python3
"""Read-only AOSP liblp logical partition extraction from a GPT embedded super.

This is a minimal parser for standard, non-sparse, single-block-device AOSP
LP metadata version 10.0-10.2. It checks geometry/header/tables checksums,
all extent bounds and partition selection. It never writes the input image.
For more complex formats, stop instead of trying a guessed offset.
"""
import argparse
import hashlib
import os
import pathlib
import re
import struct
import sys

SECTOR = 512
MAGIC_GEOMETRY = 0x616C4467
MAGIC_HEADER = 0x414C5030
CHUNK = 4 * 1024 * 1024


def fail(message):
    raise ValueError(message)


def uint32(buffer, off):
    return struct.unpack_from('<I', buffer, off)[0]


def verify_sha(buffer, check_start, check_end, expected):
    data = bytearray(buffer)
    data[check_start:check_end] = b'\0' * (check_end - check_start)
    return hashlib.sha256(data).digest() == expected


def read_at(f, absolute, size, disk_size):
    if absolute < 0 or size < 0 or absolute + size > disk_size:
        fail('이미지 범위를 벗어난 데이터 접근')
    f.seek(absolute)
    result = f.read(size)
    if len(result) != size:
        fail('이미지에서 요청한 바이트를 읽지 못했습니다')
    return result


def parse_geometry(f, super_offset, super_length, disk_size):
    for relative in (4096, 8192):
        data = read_at(f, super_offset + relative, 52, disk_size)
        magic, sz = struct.unpack_from('<II', data)
        if magic != MAGIC_GEOMETRY or sz != 52:
            continue
        if not verify_sha(data[:sz], 8, 40, data[8:40]):
            continue
        maxsize, slots, blocksz = struct.unpack_from('<III', data, 40)
        if (maxsize < 512 or maxsize > 4 * 1024 * 1024 or
                maxsize % 512 or not 1 <= slots <= 4 or
                blocksz < 512 or blocksz % 512):
            fail('비정상적인 LP geometry 크기/슬롯')
        if 12288 + 2 * maxsize * slots > super_length:
            fail('super 메타데이터가 파티션 범위를 벗어납니다')
        return maxsize, slots
    fail('올바른 LP geometry 및 SHA256 체크섬을 찾지 못했습니다')


def parse_metadata(f, pos, maxsize, disk_size):
    prefix = read_at(f, pos, 128, disk_size)
    magic, major, minor, hsz = struct.unpack_from('<IHHI', prefix)
    if magic != MAGIC_HEADER or major != 10 or minor > 2:
        fail('지원하지 않는 LP 메타데이터 버전 또는 magic')
    if hsz != (256 if minor >= 2 else 128):
        fail('올바르지 않은 LP header 크기')
    hdr = read_at(f, pos, hsz, disk_size)
    if not verify_sha(hdr, 12, 44, hdr[12:44]):
        fail('LP header SHA256 검사 실패')
    tsize = uint32(hdr, 44)
    if tsize > maxsize - hsz:
        fail('LP 테이블 크기가 메타데이터 최대값을 벗어납니다')
    tables = read_at(f, pos + hsz, tsize, disk_size)
    if hashlib.sha256(tables).digest() != hdr[48:80]:
        fail('LP 테이블 SHA256 검사 실패')

    def entries(offset, expected_size):
        start, count, sz = struct.unpack_from('<III', hdr, offset)
        if sz != expected_size or count > 100000 or start + count * sz > tsize:
            fail(f'LP 테이블 descriptor 비정상: {offset}')
        return [tables[start + i * sz:start + (i + 1) * sz] for i in range(count)]

    parts = entries(80, 52)
    extents = entries(92, 24)
    entries(104, 48)  # Groups: bounds/entry-size validation
    devices = entries(116, 64)
    if len(devices) != 1:
        fail('여러 물리 super 장치는 지원하지 않습니다')
    return parts, extents


def extract(f, disk_size, off, length, outdir):
    if not (0 <= off < disk_size and 0 < length <= disk_size - off):
        fail('super 오프셋/길이가 이미지 크기보다 큽니다')
    meta_max, slots = parse_geometry(f, off, length, disk_size)
    # Standard primary metadata slot 0 starts after reserved 4KiB + two 4KiB geometry blocks.
    parts, extents = parse_metadata(f, off + 12288, meta_max, disk_size)
    wanted = re.compile(r'^(system|system_ext|product|vendor)(?:_[ab])?$')
    result = []
    for part in parts:
        name = part[:36].split(b'\0', 1)[0].decode('ascii', errors='strict')
        if not wanted.fullmatch(name):
            continue
        first, count = struct.unpack_from('<II', part, 40)
        if not count or first + count > len(extents):
            fail(f'파티션 {name}: extent 범위 오류')
        plan = []
        total = 0
        for extent in extents[first:first + count]:
            sectors, target_type, target_data, source = struct.unpack('<QIQI', extent)
            n = sectors * SECTOR
            if n == 0 or n > 8 * 1024 ** 3 or total + n > 8 * 1024 ** 3:
                fail(f'파티션 {name}: 비정상적인 extent 크기')
            if target_type not in (0, 1):
                fail(f'파티션 {name}: 지원하지 않는 extent 종류 {target_type}')
            if target_type == 0 and (source != 0 or target_data * SECTOR + n > length):
                fail(f'파티션 {name}: 물리 이미지 범위 오류')
            if target_type == 1 and (source != 0 or target_data != 0):
                fail(f'파티션 {name}: 비정상적인 zero extent')
            plan.append((target_type, target_data * SECTOR, n))
            total += n
        result.append((name, total, plan))
    if not result:
        fail('system/system_ext/product/vendor 논리 파티션이 없습니다')
    outdir.mkdir(parents=True, exist_ok=True)
    for name, total, plan in result:
        path = outdir / (name + '.img')
        if path.exists():
            fail(f'출력 파일이 이미 존재합니다: {path}')
        print(f'[INFO] LP 파티션 {name}: {total} bytes', flush=True)
        with path.open('xb') as outf:
            outf.truncate(total)
            pos = 0
            for typ, source_offset, n in plan:
                if typ == 0:
                    remain = n
                    f.seek(off + source_offset)
                    outf.seek(pos)
                    while remain:
                        chunk = f.read(min(CHUNK, remain))
                        if not chunk:
                            fail(f'파티션 {name}: 소스 읽기 실패')
                        outf.write(chunk)
                        remain -= len(chunk)
                pos += n
    return len(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('disk_image')
    parser.add_argument('super_offset', type=int)
    parser.add_argument('super_length', type=int)
    parser.add_argument('output_dir')
    a = parser.parse_args()
    path = pathlib.Path(a.disk_image)
    with path.open('rb') as stream:
        count = extract(stream, path.stat().st_size, a.super_offset, a.super_length,
                        pathlib.Path(a.output_dir))
    print(f'[INFO] 총 {count}개 LP 파티션 추출 완료', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, struct.error, UnicodeError) as error:
        print(f'[ERROR] {error}', file=sys.stderr)
        sys.exit(1)
