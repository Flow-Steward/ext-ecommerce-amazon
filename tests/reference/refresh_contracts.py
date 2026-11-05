#!/usr/bin/env python3
"""Rewrite every generated contract from the closed runtime registry.

Offline and deterministic: it reads ``runtime/catalog.py`` and nothing else.
Run it from the bundle root after changing the registry, then run the tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

BUNDLE_ROOT = Path(__file__).resolve().parents[2]
for entry in (BUNDLE_ROOT, BUNDLE_ROOT / "tests" / "reference"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from build_contracts import (  # noqa: E402
    GENERATED_FILES,
    GENERATED_TEXT,
    dump,
    manifest_with_artifact_policies,
    manifest_with_external_effects,
)


def main() -> int:
    for relative, builder in GENERATED_FILES.items():
        path = BUNDLE_ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump(builder()), encoding="utf-8")
        print(f"wrote {relative}")
    for relative, builder in GENERATED_TEXT.items():
        path = BUNDLE_ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(builder(), encoding="utf-8")
        print(f"wrote {relative}")
    manifest = BUNDLE_ROOT / "extension.yaml"
    text = manifest.read_text(encoding="utf-8")
    text = manifest_with_external_effects(text)
    text = manifest_with_artifact_policies(text)
    manifest.write_text(text, encoding="utf-8")
    print("wrote extension.yaml external_effects and artifact_policies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
