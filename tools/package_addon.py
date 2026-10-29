#!/usr/bin/env python3
import os
import re
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
PACKAGE = "gvz_pzz_importer"
FILES = ("__init__.py", "core.py")


def addon_version():
    source = open(os.path.join(ROOT, "__init__.py"), encoding="utf-8").read()
    match = re.search(r'"version":\s*\((\d+),\s*(\d+),\s*(\d+)\)', source)
    if not match:
        raise SystemExit("could not read version from __init__.py")
    return ".".join(match.groups())


def main():
    os.makedirs(DIST, exist_ok=True)
    version = addon_version()
    output = os.path.join(DIST, "%s-%s.zip" % (PACKAGE, version))
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(os.path.join(ROOT, name), os.path.join(PACKAGE, name))
    print(output)


if __name__ == "__main__":
    main()
