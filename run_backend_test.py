# run_backend_test.py
import sys
from slides_backend import run_back_end

# Mock the ipywidgets Output class for command-line execution
class DummyStatusOutput:
    def __enter__(self):
        pass
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
    def clear_output(self):
        pass

if __name__ == "__main__":
    print("--- Starting Backend Test Script ---")

    # Hardcoded URLs for testing
    master_url = "https://docs.google.com/presentation/d/1z-0M3lxGkD2J591FXWDO-3rYNfckHIY9xs1-tw6G-mA"
    dest_url = "https://docs.google.com/presentation/d/1CLNSV1AQELkm3fiTBB5bTGGYrZNpPWnuhBWccLbJ9r4"
    table_format = "Format 2: Dual Header (Rows 0 & 1 Combined)"

    print(f"Master URL: {master_url}")
    print(f"Destination URL: {dest_url}")
    print(f"Table Format: {table_format}\n")

    # Instantiate the dummy output
    status_output = DummyStatusOutput()

    # Call the backend function
    log_file, status_code = run_back_end(
        master_url,
        dest_url,
        table_format,
        status_output
    )

    print("\n--- Backend Process Complete ---")
    print(f"Status Code: {status_code}")
    print(f"Log File: {log_file}\n")
    if status_code == 0:
        print("Result: **Run completed with no inconsistencies.**")
    else:
        print("Result: **Inconsistencies found. Please check the log file for details.**")
