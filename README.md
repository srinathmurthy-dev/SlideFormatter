# Slide Synchronizer & Validation Tool

This tool synchronizes content and validates data between Google Slides presentations. It uses a Jupyter Notebook to provide a user interface for running the process.

## Setup Instructions

To run the user interface in JupyterLab, you must have the correct packages and extensions installed. Please follow these steps to set up your environment.

### 1. Install Required Python Packages

Run the following command in your terminal to install the necessary libraries. This single command installs `ipywidgets` for the UI and `jupyterlab` itself.

```bash
python3 -m pip install --upgrade ipywidgets jupyterlab
```

### 2. Run the UI

After the installation is complete, the UI can be launched from a Jupyter Notebook (like the included `Launch UI.ipynb`).

**Important:** The function `display_ui` now returns the UI object. You must have a final line in your cell that references the object to make Jupyter display it.

1.  **Restart JupyterLab**: If JupyterLab is already running, shut it down completely in your terminal (usually with `Ctrl+C`) and restart it.
2.  **Refresh Your Browser**: Do a full refresh of your browser tab (`Ctrl+Shift+R` or `Cmd+Shift+R`).
3.  **Launch the Notebook**: Open a notebook and use the following code in a cell:

    ```python
    from slides_ui import display_ui

    # Create the UI object
    my_ui = display_ui()

    # Display the UI by placing the object as the last line in the cell
    my_ui
    ```
This updated method is more robust and is the standard way to render `ipywidgets` in a notebook.
