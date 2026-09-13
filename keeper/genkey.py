#!/usr/bin/env python3
"""Generate the keeper hot key. Run this yourself; the secret never leaves your machine.

Writes keeper-key.json (gitignored) and prints only the public key.
Paste the file's contents into the GitHub Actions secret KEEPER_SECRET_KEY.
"""
import json, os
from solders.keypair import Keypair

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keeper-key.json")
if os.path.exists(path):
    kp = Keypair.from_json(open(path).read())
    print("existing key at", path)
else:
    kp = Keypair()
    with open(path, "w") as f:
        f.write(kp.to_json())
    os.chmod(path, 0o600)
    print("wrote", path)
print("public key:", kp.pubkey())
