#!/usr/bin/env python3
"""Long-running keeper for a host that keeps a process alive (Render background worker). One keeper pass on every
five-minute wall-clock mark, forever. Same code, same store, same checks as the GitHub Actions run; only the clock
differs. Only one keeper may run at a time: when this is live, the Actions keeper must be disabled."""
import subprocess, sys, time, os
HERE = os.path.dirname(os.path.abspath(__file__))
print("gapless keeper loop starting", flush=True)
while True:
    time.sleep(300 - time.time() % 300)
    started = time.time()
    r = subprocess.run([sys.executable, os.path.join(HERE, "keeper.py")])
    print(f"pass exit={r.returncode} took={time.time()-started:.1f}s", flush=True)
