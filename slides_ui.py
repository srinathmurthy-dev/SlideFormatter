import os
import ipywidgets as widgets
from ipywidgets import Layout
from IPython.display import display, HTML

# Import the backend function and constants
from slides_backend import run_back_end, LOG_FILENAME

# --- UI Elements ---

# 1. Status Output Area
status_output = widgets.Output()

# 2. Logo and Title
logo_html = widgets.HTML(
    '<div style="float:left; width: 2in; height: 1in; background-color: #4285F4; color: white; text-align: center; line-height: 1in; font-size: 10px;"></div>'
)
title_html = widgets.HTML(
    '<div style="text-align: center; font-size: 24px; font-weight: bold; padding-bottom: 10px;">Slides Synchronization & Validation Tool</div>'
)
header = widgets.VBox([title_html, logo_html])

# 3. Input Controls
master_url_input = widgets.Text(
    description='Master:',
    placeholder='Google Slides URL/ID',
    value='https://docs.google.com/presentation/d/1z-0M3lxGkD2J591FXWDO-3rYNfckHIY9xs1-tw6G-mA'
)
destination_url_input = widgets.Text(
    description='Destination:',
    placeholder='Slides URL/ID or Folder URL/ID',
    value='https://docs.google.com/presentation/d/1CLNSV1AQELkm3fiTBB5bTGGYrZNpPWnuhBWccLbJ9r4'
)
table_format_dropdown = widgets.Dropdown(
    options=[
        'Format 1: Row 0/Col 0 Headers',
        'Format 2: Dual Header (Rows 0 & 1 Combined)',
        'Format 3: Row 1/Col 0 Headers (Row 0 Ignored)'
    ],
    value='Format 1: Row 0/Col 0 Headers',
    description='Table Format:',
)

interpret_parentheses_checkbox = widgets.Checkbox(
    value=True,
    description='Interpret ()',
    disabled=False,
    indent=False
)

go_button = widgets.Button(description='Go', disabled=True)

# 4. File Browsing Simulation
def file_picker(description, file_type):
    def on_click(b):
        status_output.clear_output()
        with status_output:
            print(f"Simulating Drive browsing for {file_type}...")
            print("Please manually enter the ID for now.")
    btn = widgets.Button(description=f"Browse Drive for {file_type}")
    btn.on_click(on_click)
    return widgets.VBox([widgets.Label(description), btn])

# 5. Input Validation
def validate_inputs(*args):
    is_valid = bool(master_url_input.value.strip() and destination_url_input.value.strip())
    go_button.disabled = not is_valid
    go_button.style.button_color = 'lightgreen' if is_valid else 'white'

master_url_input.observe(validate_inputs, names='value')
destination_url_input.observe(validate_inputs, names='value')
validate_inputs() # Initial check

# 6. Go Button Handler
def on_go_clicked(b):
    go_button.disabled = True
    status_output.clear_output()

    with status_output:
        print(f"Status: Starting back-end process (Format: **{table_format_dropdown.value}**)...")

    # Call the backend function, passing the UI elements and the status_output widget
    debug_log_file, status_code = run_back_end(
        master_url_input.value,
        destination_url_input.value,
        table_format_dropdown.value,
        interpret_parentheses_checkbox.value,
        status_output  # Pass the widget for real-time updates
    )

    with status_output:
        print(f"Status: Completed. Debug Log: {debug_log_file}")
        popup_message = "Completed with no inconsistencies." if status_code == 0 else "**Inconsistencies found. See log for details.**"
        print(f"\n--- POP-UP SIMULATION ---\nMESSAGE: {popup_message}\nLog File: file:///{os.path.abspath(debug_log_file)}\n--- END POP-UP ---")

    go_button.disabled = False
    validate_inputs()

go_button.on_click(on_go_clicked)

# 7. Main UI Layout
def display_ui():
    """Call this function in a Jupyter cell to display the UI."""
    ui = widgets.VBox([
        header,
        widgets.HBox([master_url_input, file_picker("Master:", "Slides")]),
        widgets.HBox([destination_url_input, file_picker("Destination:", "Slides/Folder")]),
        table_format_dropdown,
        interpret_parentheses_checkbox,
        go_button,
        widgets.Label(value="Status: Ready"),
        status_output
    ], layout=Layout(width='800px', border='1px solid lightgray', padding='10px'))

    display(ui)

# To run the UI, you would now have a cell in your notebook that just contains:
# from slides_ui import display_ui
# display_ui()
