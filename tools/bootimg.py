#!/usr/bin/env python3
"""Small, dependency-free Android boot image helper.

Only the operations needed for the POCO F7 (onyx) kernel build are
implemented:

  info            print header/layout information
  extract-kernel  dump the kernel payload
  extract-config  extract the embedded IKCONFIG (gzip) blob
  repack          replace the kernel in a kernel-only boot image

The stock onyx boot image is an Android boot header v4 image with
ramdisk_size == 0 and signature_size == 0 (the ramdisk lives in
init_boot.img).  Repacking therefore only has to replace the kernel and
keep the partition size, which is what this tool does.  AVB metadata that
was appended to the stock image is intentionally dropped: a modified
kernel invalidates it anyway and an unlocked bootloader does not require
it.
"""

from __future__ import annotations

import argparse
import gzip
import struct
import sys
import zlib
from pathlib import Path

BOOT_MAGIC = b"ANDROID!"
VENDOR_BOOT_MAGIC = b"VNDRBOOT"
DEFAULT_PAGE_SIZE = 4096


class BootImageError(Exception):
    pass


def align(value: int, page_size: int) -> int:
    return (value + page_size - 1) // page_size * page_size


def be32(buf: bytes, offset: int) -> int:
    return struct.unpack_from(">I", buf, offset)[0]


def be64(buf: bytes, offset: int) -> int:
    return struct.unpack_from(">Q", buf, offset)[0]


class BootImage:
    def __init__(self, data: bytes):
        self.data = data
        if len(data) < 48:
            raise BootImageError("file is too small to be a boot image")
        self.magic = data[:8]
        if self.magic == VENDOR_BOOT_MAGIC:
            raise BootImageError("vendor_boot images are not supported")
        if self.magic != BOOT_MAGIC:
            raise BootImageError("missing ANDROID! magic")

        self.kernel_size = struct.unpack_from("<I", data, 8)[0]
        self.ramdisk_size = struct.unpack_from("<I", data, 12)[0]
        self.os_version = struct.unpack_from("<I", data, 16)[0]
        self.header_size = struct.unpack_from("<I", data, 20)[0]
        self.header_version = struct.unpack_from("<I", data, 40)[0]
        self.cmdline = data[44:44 + 1536].split(b"\0", 1)[0].decode(
            "utf-8", "replace"
        )

        if self.header_version in (0, 1, 2):
            self.page_size = struct.unpack_from("<I", data, 36)[0] or DEFAULT_PAGE_SIZE
            self.signature_size = 0
        elif self.header_version in (3, 4):
            self.page_size = DEFAULT_PAGE_SIZE
            self.signature_size = (
                struct.unpack_from("<I", data, 1580)[0]
                if self.header_version == 4 and self.header_size >= 1584
                else 0
            )
        else:
            raise BootImageError(f"unsupported boot header v{self.header_version}")

        self.kernel_offset = align(self.header_size, self.page_size)
        self.ramdisk_offset = self.kernel_offset + align(
            self.kernel_size, self.page_size
        )
        self.signature_offset = self.ramdisk_offset + align(
            self.ramdisk_size, self.page_size
        )

    @classmethod
    def load(cls, path: str | Path) -> "BootImage":
        return cls(Path(path).read_bytes())

    @property
    def kernel(self) -> bytes:
        return self.data[self.kernel_offset:self.kernel_offset + self.kernel_size]

    @property
    def ramdisk(self) -> bytes:
        return self.data[self.ramdisk_offset:self.ramdisk_offset + self.ramdisk_size]

    @property
    def signature(self) -> bytes:
        return self.data[
            self.signature_offset:self.signature_offset + self.signature_size
        ]

    def avb_footer(self) -> dict[str, int] | None:
        if len(self.data) < 64 or self.data[-64:-60] != b"AVBf":
            return None
        footer = self.data[-64:]
        return {
            "version_major": be32(footer, 4),
            "version_minor": be32(footer, 8),
            "original_image_size": be64(footer, 12),
            "vbmeta_offset": be64(footer, 20),
            "vbmeta_size": be64(footer, 28),
        }

    def info(self) -> str:
        lines = [
            f"magic             {self.magic.decode('ascii', 'replace')}",
            f"header_version    {self.header_version}",
            f"header_size       {self.header_size}",
            f"page_size         {self.page_size}",
            f"kernel_size       {self.kernel_size}",
            f"kernel_offset     {self.kernel_offset:#x} ({self.kernel_offset})",
            f"ramdisk_size      {self.ramdisk_size}",
            f"ramdisk_offset    {self.ramdisk_offset:#x} ({self.ramdisk_offset})",
            f"signature_size    {self.signature_size}",
            f"file_size         {len(self.data)}",
            f"cmdline           {self.cmdline!r}",
        ]
        footer = self.avb_footer()
        if footer:
            lines.extend(
                f"avb_{key:<12} {value}"
                for key, value in footer.items()
            )
        return "\n".join(lines)

    def extract_config(self) -> bytes | None:
        blob = self.kernel
        start = blob.find(b"IKCFG_ST")
        if start < 0:
            return None
        start += len(b"IKCFG_ST")
        end = blob.find(b"IKCFG_ED", start)
        if end < 0:
            raise BootImageError("IKCFG_ST found without matching IKCFG_ED")
        return gzip.decompress(blob[start:end])

    def repack(self, kernel: bytes, size: int | None = None) -> bytes:
        if not kernel:
            raise BootImageError("refusing to write an empty kernel")
        if kernel[:2] != b"MZ" and b"ARM\x64" not in kernel[:0x40]:
            raise BootImageError("kernel does not look like an arm64 Image")

        partition_size = size if size is not None else len(self.data)
        needed = align(self.kernel_offset + len(kernel), self.page_size)
        if needed > partition_size:
            raise BootImageError(
                f"kernel does not fit: need {needed} bytes, partition is "
                f"{partition_size} bytes"
            )

        image = bytearray(partition_size)
        # Preserve the complete first page (header + cmdline + padding).
        header_page = min(self.page_size, len(self.data))
        image[:header_page] = self.data[:header_page]

        struct.pack_into("<I", image, 8, len(kernel))
        image[self.kernel_offset:self.kernel_offset + len(kernel)] = kernel
        return bytes(image)


def cmd_info(args: argparse.Namespace) -> int:
    print(BootImage.load(args.image).info())
    return 0


def cmd_extract_kernel(args: argparse.Namespace) -> int:
    image = BootImage.load(args.image)
    Path(args.output).write_bytes(image.kernel)
    print(f"wrote {len(image.kernel)} bytes to {args.output}")
    return 0


def cmd_extract_config(args: argparse.Namespace) -> int:
    image = BootImage.load(args.image)
    config = image.extract_config()
    if config is None:
        print("no embedded IKCONFIG found", file=sys.stderr)
        return 1
    if args.output:
        Path(args.output).write_bytes(config)
        print(f"wrote {len(config)} bytes to {args.output}")
    else:
        sys.stdout.write(config.decode("utf-8", "replace"))
    return 0


def cmd_repack(args: argparse.Namespace) -> int:
    image = BootImage.load(args.stock)
    kernel = Path(args.kernel).read_bytes()
    result = image.repack(kernel, args.size)

    output = Path(args.output)
    output.write_bytes(result)
    print(
        f"wrote {len(result)} bytes to {output} "
        f"(kernel {len(kernel)} bytes, header v{image.header_version})"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("info", help="print boot image information")
    p.add_argument("image")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("extract-kernel", help="extract the kernel payload")
    p.add_argument("image")
    p.add_argument("output")
    p.set_defaults(func=cmd_extract_kernel)

    p = sub.add_parser("extract-config", help="extract the embedded IKCONFIG")
    p.add_argument("image")
    p.add_argument("output", nargs="?")
    p.set_defaults(func=cmd_extract_config)

    p = sub.add_parser("repack", help="replace the kernel in a boot image")
    p.add_argument("--stock", required=True, help="stock boot.img")
    p.add_argument("--kernel", required=True, help="new arm64 Image")
    p.add_argument("--output", required=True, help="output boot image")
    p.add_argument(
        "--size",
        type=lambda value: int(value, 0),
        help="output image/partition size in bytes (default: stock size)",
    )
    p.set_defaults(func=cmd_repack)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (BootImageError, OSError, zlib.error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
