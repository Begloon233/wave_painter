#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""为发布目录生成 SHA256SUMS.txt（CI 与本地共用）。

用法：
    python scripts/sha256sums.py dist
    python scripts/sha256sums.py dist -o dist/SHA256SUMS.txt
"""
import argparse
import hashlib
import os
import sys

SUMS_NAME = "SHA256SUMS.txt"
CHUNK = 1 << 20


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def collect(directory, recursive=False):
    paths = []
    for root, dirs, files in os.walk(directory):
        dirs.sort()
        for name in sorted(files):
            if name == SUMS_NAME:
                continue
            paths.append(os.path.join(root, name))
        if not recursive:
            break
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description="为目录中的文件生成 SHA256SUMS.txt")
    parser.add_argument("directory", nargs="?", default="dist",
                        help="要计算校验和的目录（默认 dist）")
    parser.add_argument("-o", "--output", default=None,
                        help="输出文件（默认 <directory>/SHA256SUMS.txt）")
    parser.add_argument("-r", "--recursive", action="store_true", help="递归子目录")
    args = parser.parse_args(argv)

    directory = args.directory
    if not os.path.isdir(directory):
        print("directory not found: %s" % directory, file=sys.stderr)
        return 1

    paths = collect(directory, args.recursive)
    if not paths:
        print("no files to hash in: %s" % directory, file=sys.stderr)
        return 1

    lines = []
    for path in paths:
        rel = os.path.relpath(path, directory).replace(os.sep, "/")
        line = "%s  %s" % (sha256_of(path), rel)
        lines.append(line)
        print(line)

    output = args.output or os.path.join(directory, SUMS_NAME)
    with open(output, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("wrote %s" % output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
