import sys
import os
from slides_backend import run_back_end

class DummyStatusOutput:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def clear_output(self): pass

if __name__ == "__main__":
    MASTER_URL = 'https://docs.google.com/presentation/d/1z-0M3lxGkD2J591FXWDO-3rYNfckHIY9xs1-tw6G-mA'
    DEST_URL = 'https://docs.google.com/presentation/d/1CLNSV1AQELkm3fiTBB5bTGGYrZNpPWnuhBWccLbJ9r4'
    TABLE_FORMAT = 'Format 2: Dual Header (Rows 0 & 1 Combined)'

    print(f"Testing Backend with {TABLE_FORMAT}")
    run_back_end(MASTER_URL, DEST_URL, TABLE_FORMAT, DummyStatusOutput())
