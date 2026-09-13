#!/usr/bin/env python3
"""Single smoke check: the recorder runs and appends well-formed rows to a temp CSV."""
import csv, os, re, subprocess, sys, tempfile

import recorder

with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, "prices.csv")
    env = dict(os.environ, GAPLESS_CSV=path)
    subprocess.run([sys.executable, "recorder.py"], check=True, env=env, cwd=os.path.dirname(os.path.abspath(__file__)))
    rows = list(csv.reader(open(path, newline="")))

assert rows[0] == recorder.HEADER, rows[0]
assert len(rows) == 1 + len(recorder.TICKERS), len(rows)
for ts, ticker, mint, price, session, source in rows[1:]:
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts), ts
    assert recorder.TICKERS[ticker] == mint
    assert session in {"open", "overnight", "weekend"}, session
    assert (source == recorder.SOURCE and float(price) > 0) or (source == "error" and price == ""), (price, source)
print("smoke test passed:", rows[1:])
