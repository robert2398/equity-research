"""One command for the whole daily refresh:

    python daily.py              # login (only if the Kite session expired) -> sync -> scan -> dashboard
    python daily.py --login      # force a fresh Kite login first
    python daily.py --from scan  # resume from a step (login | sync | scan | dashboard)

Steps (each stops the run on failure):
  1. login      python -m scanner auth login   (skipped when `auth status` says the session is valid)
  2. sync       python -m scanner data sync    (Kite candles + constituents for the universe)
  3. scan       python -m scanner scan         (main scanner snapshot: Sectors / Top Movers tabs)
  4. dashboard  python bear_reversal/run_daily.py  (bhavcopy, bear-reversal, next-bull, IPO watch)

Works from any Python: it re-launches itself with the project's .venv interpreter.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STEPS = ["login", "sync", "scan", "dashboard"]
COMMANDS = {
    "login": ["-m", "scanner", "auth", "login"],
    "sync": ["-m", "scanner", "data", "sync"],
    "scan": ["-m", "scanner", "scan"],
    "dashboard": ["bear_reversal/run_daily.py"],
}


def _use_venv() -> None:
    """Re-exec under .venv so a bare `python daily.py` finds pandas, kiteconnect, etc."""
    if not VENV_PY.exists() or os.environ.get("DAILY_IN_VENV"):
        return
    if Path(sys.prefix).resolve() == (ROOT / ".venv").resolve():
        return
    env = dict(os.environ, DAILY_IN_VENV="1")
    sys.exit(subprocess.call([str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]],
                             cwd=ROOT, env=env))


def _session_valid() -> bool:
    try:
        from scanner import auth
        st = auth.status()
    except Exception as e:
        print(f"  could not check Kite session ({type(e).__name__}: {e}); logging in")
        return False
    if st.get("authenticated"):
        print(f"  Kite session valid for {st.get('user_id')} — skipping login")
        return True
    if st.get("authenticated") is None:          # network hiccup: let sync surface a real error
        print(f"  {st.get('detail')} — trying with the stored session")
        return True
    print(f"  {st.get('detail')}")
    return False


def _run(step: str) -> None:
    cmd = [sys.executable, *COMMANDS[step]]
    print(f"\n=== [{STEPS.index(step) + 1}/{len(STEPS)}] {step}: "
          f"python {' '.join(COMMANDS[step])}", flush=True)
    t0 = time.time()
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        print(f"\n!!! step '{step}' failed (exit {rc}). Fix it, then resume with: "
              f"python daily.py --from {step}")
        sys.exit(rc)
    print(f"=== {step} done in {time.time() - t0:.0f}s", flush=True)


def main() -> int:
    _use_venv()
    ap = argparse.ArgumentParser(description="Full daily refresh: login, sync, scan, dashboard")
    ap.add_argument("--login", action="store_true", help="force a fresh Kite login")
    ap.add_argument("--from", dest="start", choices=STEPS, default="login",
                    help="resume from this step")
    args = ap.parse_args()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))

    t0 = time.time()
    for step in STEPS[STEPS.index(args.start):]:
        if step == "login":
            print("=== [1/4] login: checking Kite session", flush=True)
            if not args.login and _session_valid():
                continue
        _run(step)
    print(f"\nAll steps done in {(time.time() - t0) / 60:.1f} min.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
