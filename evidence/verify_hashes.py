#!/usr/bin/env python3
"""Verify the evidence bundle against the SHA-256 list in its own README."""
import hashlib, re, sys
import os
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weekend-fill-2026-09-20")
listed = dict(re.findall(r"- `([^`]+)` `([0-9a-f]{64})`", open(f"{D}/README.md").read()))
ok = True
for name, want in listed.items():
    got = hashlib.sha256(open(f"{D}/{name}", "rb").read()).hexdigest()
    print(f"{'OK  ' if got == want else 'FAIL'} {name:62s} {got[:16]}…{got[-8:]}", flush=True); ok &= got == want
print(f"{len(listed)} files, {'all hashes match the README' if ok else 'MISMATCH'}", flush=True)
sys.exit(0 if ok else 1)
