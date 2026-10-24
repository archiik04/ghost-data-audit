"""Build code.zip for submission and verify it is safe to upload.

    python code/tools/package.py
Includes README.md and code/ only. Excludes caches, run artefacts, virtualenvs and
nested archives; fails if anything secret-looking or any dataset file slips in.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXCLUDE_DIRS = {"__pycache__", ".cache", ".venv", "venv", "node_modules", ".git", "out", ".pytest_cache"}
EXCLUDE_SUFFIX = {".pyc", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".env"}
SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{20,}|OPENAI_API_KEY\s*=\s*['\"]?sk-")


def files():
    yield ROOT / "README.md"
    for p in sorted((ROOT / "code").rglob("*")):
        if p.is_file() and not (set(p.relative_to(ROOT).parts) & EXCLUDE_DIRS) and p.suffix not in EXCLUDE_SUFFIX \
                and p.name != ".env":
            yield p


def main() -> int:
    out = ROOT / "code.zip"
    problems = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files():
            rel = p.relative_to(ROOT).as_posix()
            if rel.startswith("dataset/"):
                problems.append(f"dataset file included: {rel}")
            text = p.read_text(encoding="utf-8", errors="ignore") if p.suffix in (".py", ".md", ".txt", ".json") else ""
            if SECRET_RE.search(text) and "SECRET_RE" not in text:
                problems.append(f"secret-like content in {rel}")
            z.write(p, rel)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    required = ["README.md", "code/main.py", "code/evaluation/main.py", "code/evaluation/evaluation_report.md",
                "code/evaluation/usage_report.md"]
    problems += [f"missing required file {r}" for r in required if r not in names]
    print(f"{out}: {len(names)} files")
    for n in names:
        print("  ", n)
    for pr in problems:
        print("PROBLEM:", pr)
    if problems:
        out.unlink()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
