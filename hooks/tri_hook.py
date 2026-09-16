#!/usr/bin/env python3
"""Plugin hook entry: `python3 hooks/tri_hook.py claude|codex` reads the hook payload on stdin.

It imports the compiler from the checkout this file sits in, so it needs no installed
package and no third-party module. It fails open: any problem prints nothing and exits 0.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else "claude"
    # Grok also loads this plugin's hooks but discards an allowing hook's output, so compiling
    # there is wasted work. Grok users reach tri: through the tri-grok entry point.
    if os.environ.get("GROK_PLUGIN_ROOT"):
        return 0
    try:
        from olondunge.hooks import run_hook
    except Exception:  # noqa: BLE001 - fail open when the checkout is incomplete
        return 0
    return run_hook(host)


if __name__ == "__main__":
    sys.exit(main())
