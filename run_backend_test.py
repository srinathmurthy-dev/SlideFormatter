import sys
from slides_backend import run_back_end

# A dummy class to simulate the ipywidgets.Output context manager
# This allows the backend function to run without a real UI.
class DummyStatusOutput:
    """A mock ipywidgets.Output object that does nothing."""
    def __enter__(self):
        # This is called when entering the 'with' block.
        pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        # This is called when exiting the 'with' block.
        pass

def main():
    """
    Runs the backend synchronization process with predefined URLs.
    This script is a command-line alternative to the Jupyter UI.
    """
    print("--- Starting Backend Test Script ---")

    # Default URLs from the original UI
    master_url = 'https://docs.google.com/presentation/d/1CLNSV1AQELkm3fiTBB5bTGGYrZNpPWnuhBWccLbJ9r4'
    destination_url = 'https://docs.google.com/presentation/d/1CLNSV1AQELkm3fiTBB5bTGGYrZNpPWnuhBWccLbJ9r4'
    table_format = 'Format 1: Row 0/Col 0 Headers'

    # Create an instance of our dummy UI output widget
    dummy_output = DummyStatusOutput()

    print(f"Master URL: {master_url}")
    print(f"Destination URL: {destination_url}")
    print(f"Table Format: {table_format}\n")

    # Call the main backend function
    # The print statements from the backend will go to the console.
    log_file, status_code = run_back_end(
        master_url,
        destination_url,
        table_format,
        dummy_output
    )

    print("\n--- Backend Process Complete ---")
    print(f"Status Code: {status_code}")
    print(f"Log File: {log_file}")

    if status_code == 0:
        print("\nResult: Completed with no inconsistencies.")
    else:
        print("\nResult: **Inconsistencies found. Please check the log file for details.**")
        sys.exit(1) # Exit with an error code to indicate failure

if __name__ == "__main__":
    main()
