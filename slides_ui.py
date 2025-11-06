import os
import ipywidgets as widgets
from ipywidgets import Layout
from IPython.display import display

# Import the backend function
from slides_backend import run_back_end, LOG_FILENAME

# --- UI Elements (Final Corrected Definition) ---
status_output = widgets.Output()

logo_html = widgets.HTML(
    '<div style="float:left; width: 2in; height: 1in; background-color: #4285F4; color: white; text-align: center; line-height: 1in; font-size: 10px;"></div>'
)
title_html = widgets.HTML(
    '<div style="text-align: center; font-size: 24px; font-weight: bold; padding-bottom: 10px;">Slides Synchronization & Validation Tool</div>'
)
header = widgets.VBox([title_html, logo_html])

master_url_input = widgets.Text(
    description='Master:',
    placeholder='Google Slides URL/ID or File Name',
    value='https://docs.google.com/presentation/d/1z-0M3lxGkD2J591FXWDO-3rYNfckHIY9xs1-tw6G-mA'
)
destination_url_input = widgets.Text(
    description='Destination:',
    placeholder='Slides URL/ID or Folder URL/ID',
    value='https://docs.google.com/presentation/d/1CLNSV1AQELkm3fiTBB5bTGGYrZNpPWnuhBWccLbJ9r4'
)
table_format_dropdown = widgets.Dropdown(
    options=['Format 1: Row 0/Col 0 Headers', 'Format 2: Dual Header (Rows 0 & 1 Combined)'],
    value='Format 1: Row 0/Col 0 Headers',
    description='Table Format:',
)
go_button = widgets.Button(description='Go', disabled=True)

def file_picker(description, file_type):
    def on_click(b):
        status_output.clear_output()
        with status_output:
            print(f"Simulating Drive browsing for {file_type}...")
            print("Please manually enter the ID for now.")
    btn = widgets.Button(description=f"Browse Drive for {file_type}")
    btn.on_click(on_click)
    return widgets.VBox([widgets.Label(description), btn])

def validate_inputs():
    is_valid = bool(master_url_input.value.strip() and destination_url_input.value.strip())
    go_button.disabled = not is_valid
    if is_valid:
        go_button.style.button_color = 'lightgreen'
    else:
        go_button.style.button_color = 'white'

master_url_input.observe(lambda change: validate_inputs(), names='value')
destination_url_input.observe(lambda change: validate_inputs(), names='value')
validate_inputs()

def on_go_clicked(b):
    go_button.disabled = True
    status_output.clear_output()
    with status_output:
        print(f"Status: Starting back-end process (Table Format: **{table_format_dropdown.value}**)...")

    # Pass status_output to the backend function
    debug_link, status_code = run_back_end(
        master_url_input.value,
        destination_url_input.value,
        table_format_dropdown.value,
        status_output
    )

    with status_output:
        print(f"Status: Completed. Debug Log: {debug_link}")
        popup_message = "Completed with no inconsistencies" if status_code == 0 else "**Inconsistencies found (See log for details).**"
        print(f"\n\n--- POP-UP MESSAGE SIMULATION ---\n")
        print(f"MESSAGE: {popup_message}")
        print(f"Clicking OK will attempt to open the Debug Log URL: file:///{os.path.abspath(debug_link)}")
        print(f"--- END POP-UP SIMULATION ---")

    go_button.disabled = False
    validate_inputs()

go_button.on_click(on_go_clicked)

ui = widgets.VBox([
    header,
    widgets.HBox([master_url_input, file_picker("Master:", "Slides")]),
    widgets.HBox([destination_url_input, file_picker("Destination:", "Slides/Folder")]),
    widgets.HBox([table_format_dropdown]),
    widgets.HBox([go_button]),
    widgets.Label(value="Status: Ready"),
    status_output
], layout=Layout(width='800px', border='1px solid lightgray', padding='10px'))

def display_ui():
    display(ui)
