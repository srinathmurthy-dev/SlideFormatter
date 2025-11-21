import sys
import os
from slides_backend import run_back_end

# Mock class to simulate the ipywidgets Output widget behavior
class DummyStatusOutput:
    def __enter__(self):
        # In a real ipywidgets.Output, this captures stdout.
        # Here we just let stdout go to the console.
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass

    def clear_output(self):
        pass

if __name__ == "__main__":
    # Configuration
    MASTER_URL = 'https://docs.google.com/presentation/d/1z-0M3lxGkD2J591FXWDO-3rYNfckHIY9xs1-tw6G-mA'
    DEST_URL = 'https://docs.google.com/presentation/d/1CLNSV1AQELkm3fiTBB5bTGGYrZNpPWnuhBWccLbJ9r4'
    TABLE_FORMAT = 'Format 2: Dual Header (Rows 0 & 1 Combined)'

    print("--- STARTING BACKEND TEST ---")
    print(f"Master: {MASTER_URL}")
    print(f"Dest: {DEST_URL}")
    print(f"Format: {TABLE_FORMAT}")

    # Instantiate the dummy widget
    status_output = DummyStatusOutput()

    # Run the backend
    try:
        log_file, status_code = run_back_end(MASTER_URL, DEST_URL, TABLE_FORMAT, status_output)

        print("\n--- TEST COMPLETE ---")
        print(f"Log File: {log_file}")
        print(f"Status Code: {status_code}")

        if status_code == 0:
            print("SUCCESS: No inconsistencies found.")
            sys.exit(0)
        else:
            print("FAILURE: Inconsistencies found or error occurred.")
            sys.exit(1)
    except Exception as e:
        print(f"\nCRITICAL FAILURE: {e}")
        sys.exit(1)
