import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog
import os
import threading
import time
from AppKit import NSWorkspace, NSObject
from Foundation import NSLog
from playsound import playsound
from functools import partial # Added for callbacks with arguments
import json

# Global state
monitored_apps = {}  # {"app_path": "sound_file_name.mp3"}
sound_files = []
symlink_ui_sections = {} # Replaces symlink_row_data and dynamic_symlink_ui_container
applied_file_modifications = {} # Stores info about direct file symlinks: {"original_path": {"backup_path": "...", "target_linked_to": "..."}}
app_default_symlink_sources = {} # NEW: {"app_path": "default_source_sound_for_symlinks.wav"}

APP_CONFIG_FILE = "app_monitor_config.json"
SOUNDS_DIR = "sounds"

# Global reference for the main notebook
app_notebook = None

# --- macOS Specific App Monitoring ---
class AppDelegate(NSObject):
    def applicationDidLaunch_(self, notification):
        app_info = notification.userInfo()
        launched_app_path = app_info.get('NSApplicationPath')
        launched_app_name = app_info.get('NSApplicationName')
        # NSLog(f"App launched: {launched_app_name} at {launched_app_path}")

        if launched_app_path in monitored_apps:
            sound_to_play = monitored_apps[launched_app_path]
            full_sound_path = os.path.join(SOUNDS_DIR, sound_to_play)
            NSLog(f"Monitored app launched: {launched_app_name}. Playing sound: {full_sound_path}")
            try:
                # Run playsound in a separate thread to avoid blocking GUI or notification handler
                threading.Thread(target=play_sound_thread, args=(full_sound_path,), daemon=True).start()
            except Exception as e:
                NSLog(f"Error playing sound {full_sound_path}: {e}")
                # Optionally show a GUI error if critical, but NSLog might be enough for background task
                # messagebox.showerror("Sound Error", f"Could not play sound for {launched_app_name}: {e}")


def play_sound_thread(sound_path):
    try:
        playsound(sound_path)
    except Exception as e:
        NSLog(f"playsound error in thread: {e}")
        # Consider how to report this error if necessary, maybe a log file or a status bar update

def start_app_monitoring():
    # This function needs to run the Cocoa event loop without blocking Tkinter.
    # Typically, this is done by running it in a separate thread.
    try:
        delegate = AppDelegate.alloc().init()
        nc = NSWorkspace.sharedWorkspace().notificationCenter()
        nc.addObserver_selector_name_object_(
            delegate,
            "applicationDidLaunch:",
            "NSWorkspaceDidLaunchApplicationNotification",
            None
        )
        NSLog("App monitoring started.")
        # Keep this thread alive to process notifications
        # This part is tricky as NSApplication.sharedApplication().run() would block.
        # For background notifications, just adding the observer might be enough
        # if the main application (Python script) is running an event loop (like Tkinter's).
        # However, for robust ObjC event processing, a dedicated run loop is usually needed.
        # Let's test if notifications are received without an explicit run loop in this thread.
        # If not, we might need to use Foundation.NSRunLoop.currentRunLoop().run()
        # or similar, but that would block the thread.
        # A common pattern is to have the ObjC part run its own loop in a thread.
        # For now, we assume adding the observer starts the machinery.
        # Keeping the thread alive might be needed if the observer gets GC'd.
        # For PyObjC, the main Python script's lifecycle often keeps objects alive.
        while True: # Keep thread alive to ensure delegate isn't garbage collected prematurely.
            time.sleep(10) # Check periodically or just keep thread running.
                           # This is not ideal; proper ObjC runloop integration is better.

    except Exception as e:
        NSLog(f"Failed to start app monitoring: {e}")
        # Fallback or error message to user
        # messagebox.showerror("Monitoring Error", f"Could not start app monitoring: {e}")


def preview_sound(sound_path_or_name, parent_for_dialog):
    """Plays the given sound file. Handles relative paths from SOUNDS_DIR and absolute paths."""
    if not sound_path_or_name or sound_path_or_name == "None" or sound_path_or_name == "Not Set" or sound_path_or_name == "<Browse for target>":
        messagebox.showwarning("No Sound Selected", "No sound file is selected or specified for preview.", parent=parent_for_dialog)
        return

    # Check if it's a full path or just a name from SOUNDS_DIR
    if os.path.isabs(sound_path_or_name):
        full_sound_path = sound_path_or_name
    else:
        # Assume it's a name from SOUNDS_DIR
        full_sound_path = os.path.join(SOUNDS_DIR, sound_path_or_name)

    if not os.path.exists(full_sound_path):
        messagebox.showerror("File Not Found", f"The sound file was not found:\n{full_sound_path}", parent=parent_for_dialog)
        return
    
    NSLog(f"Attempting to preview sound: {full_sound_path}")
    try:
        threading.Thread(target=play_sound_thread, args=(full_sound_path,), daemon=True).start()
    except Exception as e:
        NSLog(f"Error trying to start preview thread for {full_sound_path}: {e}")
        messagebox.showerror("Preview Error", f"Could not play sound: {e}", parent=parent_for_dialog)

# --- Symlinking Feature Functions ---

# [Original start_symlink_process - REMOVED as new_trigger_for_app_sounds_symlink_ui replaces it]
# [Original browse_app_internal_sounds - REMOVED, logic integrated into new_trigger_for_app_sounds_symlink_ui]
# [Original create_symlink_for_chosen_sound - REMOVED, replaced by handle_save_symlink with different logic]
# [Original start_symlink_from_folder_process - REMOVED as new_trigger_for_folder_sounds_symlink_ui replaces it]
# [Original browse_sounds_in_custom_folder - REMOVED, Toplevel UI part removed, file listing logic moved to get_sounds_from_custom_folder]


# --- New Symlinking Feature: From Custom Folder (Helper) ---
# Modified to return list of sounds instead of showing UI
def get_sounds_from_custom_folder(folder_path):
    potential_sounds = []
    try:
        for item in os.listdir(folder_path):
            full_item_path = os.path.join(folder_path, item)
            if item.lower().endswith((".wav", ".mp3", ".aiff", ".m4a")) and os.path.isfile(full_item_path):
                potential_sounds.append(full_item_path)
    except Exception as e:
        # Removed parent=root as this is now a utility function
        messagebox.showerror("Error", f"Could not read folder contents: {e}") 
        return []
    return potential_sounds

# --- Symlink Creation UI and Logic (New/Refactored) ---

# Global reference to the frame within the canvas that holds symlink rows
# This will be assigned in setup_gui
# sections_host_frame_ref = None # ADDED: Will hold the frame inside the canvas that hosts all sections - NOW OBSOLETE

def handle_select_target(row_index):
    global symlink_ui_sections, root
    desktop_path = os.path.expanduser("~/Desktop")
    filepath = filedialog.askopenfilename(
        title="Select Target Sound File",
        initialdir=desktop_path,
        filetypes=[("Sound files", "*.mp3 *.wav *.aiff *.m4a"), ("All files", "*.*")],
        parent=root 
    )
    if filepath:
        symlink_ui_sections[row_index]['target_path'] = filepath
        target_label = symlink_ui_sections[row_index]['widgets']['target_display_label']
        target_label.config(text=os.path.basename(filepath))
    else:
        symlink_ui_sections[row_index]['target_path'] = None
        target_label = symlink_ui_sections[row_index]['widgets']['target_display_label']
        target_label.config(text="<No target selected>")

def handle_save_symlink(row_index):
    global symlink_ui_sections, root
    data = symlink_ui_sections[row_index]
    target_path = data.get('target_path')
    original_path = data.get('original_path') # This is the file to be replaced by a symlink

    if not target_path:
        messagebox.showwarning("Missing Target", "Please select a target sound file first.", parent=root)
        return

    if not original_path:
        messagebox.showerror("Error", "Original sound path is missing. Cannot proceed.", parent=root)
        return

    if not os.path.exists(target_path):
        messagebox.showerror("Error", f"Target sound file does not exist:\\n{target_path}", parent=root)
        return

    # Confirm with the user before modifying the original file
    confirm_message = f"This will replace:\\n{original_path}\\n\\nwith a symlink to:\\n{target_path}\\n\\nThe original file will be backed up as {os.path.basename(original_path)}.bak.\\nProceed?"
    if not messagebox.askyesno("Confirm Action", confirm_message, parent=root):
        return

    backup_path = original_path + ".bak"

    try:
        # Handle existing original_path (file or symlink) and backup
        if os.path.islink(original_path):
            # If original_path is already a symlink, just remove it before creating the new one.
            # No backup of the symlink itself is typically needed.
            os.remove(original_path)
            NSLog(f"Removed existing symlink at: {original_path}")
        elif os.path.exists(original_path):
            # If original_path is a file, attempt to back it up.
            if os.path.exists(backup_path):
                if not messagebox.askyesno("Overwrite Backup?", 
                                         f"Backup file {backup_path} already exists. Overwrite it?", 
                                         parent=root):
                    return
                os.remove(backup_path)
                NSLog(f"Removed existing backup: {backup_path}")
            os.rename(original_path, backup_path)
            NSLog(f"Backed up {original_path} to {backup_path}")

        # Create the new symlink
        os.symlink(target_path, original_path)
        messagebox.showinfo("Success", 
                            f"Successfully applied symlink:\\n{original_path} \\n-> {target_path}\\n\\nOriginal file backed up as: {os.path.basename(backup_path)}", 
                            parent=root)
        NSLog(f"Symlink created: {original_path} -> {target_path}")

        # Record the modification
        applied_file_modifications[original_path] = {
            "backup_path": backup_path,
            "target_linked_to": target_path
        }
        save_config() # Save config immediately after successful modification
        NSLog(f"Recorded symlink: {original_path} -> {target_path}")

        # Note: We no longer need to call load_sound_files() here unless original_path was in SOUNDS_DIR
        # and we want to refresh that view for some reason. The primary action is modifying the original file path.

    except Exception as e:
        messagebox.showerror("Symlink Error", f"Could not apply symlink: {e}\\n\\nEnsure you have permissions to modify the original file location.", parent=root)
        NSLog(f"Error applying symlink: {e}")

def display_sounds_for_symlinking(original_sound_paths):
    global sections_host_frame_ref, root

    if sections_host_frame_ref is None:
        NSLog("Error: sections_host_frame_ref is not initialized in setup_gui.")
        messagebox.showerror("UI Error", "Symlink UI container not ready.", parent=root)
        return

    for widget in sections_host_frame_ref.winfo_children():
        widget.destroy()
    symlink_ui_sections.clear()

    if not original_sound_paths:
        ttk.Label(sections_host_frame_ref, text="No sound files found in the selected source.").pack(pady=10, padx=5)
        # Ensure canvas updates scrollregion if it was previously large and now small
        # sections_host_frame_ref.master.event_generate("<Configure>") # OBSOLETE, sections_host_frame_ref is removed
        return

    header_frame = ttk.Frame(sections_host_frame_ref)
    header_frame.pack(fill=tk.X, pady=(0, 5), padx=5)
    # Simple header for now, can be expanded
    ttk.Label(header_frame, text="Original Sound", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, sticky="w", padx=2)
    ttk.Label(header_frame, text="Target Sound", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=2, columnspan=2, sticky="w", padx=2)
    ttk.Label(header_frame, text="Action", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=4, sticky="w", padx=2)


    for i, original_path in enumerate(original_sound_paths):
        original_filename = os.path.basename(original_path)
        
        row_frame = ttk.Frame(sections_host_frame_ref) # Removed padding here, add to internal widgets if needed
        row_frame.pack(fill=tk.X, pady=2, padx=5)

        ttk.Label(row_frame, text=original_filename, width=25, anchor="w", relief="groove", padding=2).grid(row=0, column=0, sticky="ew", padx=(0,2))
        ttk.Label(row_frame, text="->", anchor="center").grid(row=0, column=1, padx=2)
        
        btn_select_target = ttk.Button(row_frame, text="Select Target", command=partial(handle_select_target, i), width=15)
        btn_select_target.grid(row=0, column=2, sticky="ew", padx=2)
        
        lbl_target_display = ttk.Label(row_frame, text="<No target selected>", width=20, anchor="w", relief="groove", padding=2)
        lbl_target_display.grid(row=0, column=3, sticky="ew", padx=2)
        
        btn_save_symlink = ttk.Button(row_frame, text="Apply Symlink to Original", command=partial(handle_save_symlink, i), width=22)
        btn_save_symlink.grid(row=0, column=4, sticky="ew", padx=2)
        
        # Configure column weights for responsiveness if desired, e.g. row_frame.columnconfigure(0, weight=1) etc.

        symlink_ui_sections[i] = {
            'original_path': original_path,
            'target_path': None,
            'widgets': {
                'target_display_label': lbl_target_display
            }
        }
    # After populating, ensure the canvas scrollregion is updated.
    # The binding on sections_host_frame_ref <Configure> should handle this.
    # Force a configure event in case the size didn't change but content did, or it's the first population.
    # sections_host_frame_ref.master.event_generate("<Configure>") # OBSOLETE, sections_host_frame_ref is removed

# --- Sound Management ---
def load_sound_files():
    global sound_files
    if not os.path.exists(SOUNDS_DIR):
        os.makedirs(SOUNDS_DIR)
        messagebox.showinfo("Sounds Folder Created",
                            f"A '{SOUNDS_DIR}' folder has been created. Please add your sound files (e.g., .mp3, .wav) there and restart.")
        sound_files = []
        return

    print(f"[Debug] Checking for sounds in: {os.path.abspath(SOUNDS_DIR)}")
    try:
        all_items = os.listdir(SOUNDS_DIR)
        print(f"[Debug] Items found in '{SOUNDS_DIR}': {all_items}")
        sound_files = [
            f for f in all_items
            if os.path.isfile(os.path.join(SOUNDS_DIR, f)) and
               (f.lower().endswith(".mp3") or f.lower().endswith(".wav"))
        ]
        print(f"[Debug] Filtered sound files: {sound_files}")
        if not sound_files:
            messagebox.showwarning("No Sounds Found", f"No .mp3 or .wav files found in the '{SOUNDS_DIR}' directory. Please ensure files have .mp3 or .wav extensions (case-insensitive).")
    except Exception as e:
        messagebox.showerror("Sound Load Error", f"Error loading sounds from '{SOUNDS_DIR}': {e}")
        print(f"[Debug] Exception in load_sound_files: {e}")
        sound_files = []
    update_sound_dropdown()


# --- Configuration Persistence ---
def load_config():
    global monitored_apps, sound_files, applied_file_modifications, app_default_symlink_sources
    try:
        if os.path.exists(APP_CONFIG_FILE):
            with open(APP_CONFIG_FILE, 'r') as f:
                data = json.load(f)
                monitored_apps = data.get("monitored_apps", {})
                applied_file_modifications = data.get("applied_file_modifications", {})
                app_default_symlink_sources = data.get("app_default_symlink_sources", {})
                # Ensure sound_files is reset or managed appropriately if loaded from config
                # sound_files = data.get("sound_files", []) # Example if sound_files were also in config
    except FileNotFoundError:
        NSLog(f"Config file {APP_CONFIG_FILE} not found. Starting with empty configuration.")
        monitored_apps = {}
        applied_file_modifications = {}
        app_default_symlink_sources = {}
    except json.JSONDecodeError:
        NSLog(f"Error decoding JSON from {APP_CONFIG_FILE}. Starting with empty/default configuration.")
        # Optionally, attempt to backup the corrupted file and notify user
        messagebox.showerror("Config Error", f"Could not parse {APP_CONFIG_FILE}. Check console for details. Using default settings.")
        monitored_apps = {}
        applied_file_modifications = {}
        app_default_symlink_sources = {}
    except Exception as e:
        NSLog(f"Unexpected error loading config: {e}")
        messagebox.showerror("Config Load Error", f"An unexpected error occurred: {e}")
        # Fallback to defaults
        monitored_apps = {}
        applied_file_modifications = {}
        app_default_symlink_sources = {}


def save_config():
    global monitored_apps, applied_file_modifications, app_default_symlink_sources, root
    config_data = {
        "monitored_apps": monitored_apps,
        "applied_file_modifications": applied_file_modifications,
        "app_default_symlink_sources": app_default_symlink_sources
    }
    try:
        with open(APP_CONFIG_FILE, 'w') as f:
            json.dump(config_data, f, indent=4)
        NSLog(f"Configuration saved to {APP_CONFIG_FILE}")
    except Exception as e:
        NSLog(f"Error saving config: {e}")
        messagebox.showerror("Config Save Error", f"Could not save configuration to {APP_CONFIG_FILE}: {e}", parent=root if 'root' in globals() and root else None)


def save_config_and_notify():
    """Saves the configuration and shows a notification message."""
    global root # Ensure root is accessible for messagebox parent
    save_config() # Calls the existing save_config function
    messagebox.showinfo("Settings Saved", "All application settings have been successfully saved.", parent=root if root and root.winfo_exists() else None)


# --- GUI Functions ---
def add_app():
    app_path = filedialog.askopenfilename(
        title="Select Application Bundle",
        initialdir="/Applications",
        filetypes=[("All files", "*.*")]  # Changed to be more general
    )
    print(f"[Debug] filedialog.askopenfilename returned: {app_path}")
    if app_path and app_path.endswith(".app"):
        app_name = os.path.basename(app_path)
        if app_path not in monitored_apps:
            # Default to first available sound or None
            default_sound = sound_files[0] if sound_files else "None"
            monitored_apps[app_path] = default_sound
            update_app_list()
            save_config()
        else:
            messagebox.showinfo("App Exists", f"{app_name} is already being monitored.")
    elif app_path:
        messagebox.showerror("Invalid Selection", "Please select a valid .app bundle.")


def remove_app():
    messagebox.showinfo("Deprecated", "This global Remove App button is deprecated. Use the remove button within each app's tab.")
    NSLog("Global remove_app button called. This should be handled by per-tab buttons.")


def remove_selected_app(app_path_to_remove):
    """Removes the specified app from monitoring and reverts its symlinks."""
    global monitored_apps, applied_file_modifications

    if app_path_to_remove not in monitored_apps:
        messagebox.showerror("Error", f"App {app_path_to_remove} not found in monitored list.")
        return

    app_name = os.path.basename(app_path_to_remove)
    if not messagebox.askyesno("Confirm Removal", f"Are you sure you want to stop monitoring {app_name} and revert its symlinks?"):
        return

    del monitored_apps[app_path_to_remove]
    NSLog(f"Stopped monitoring app: {app_path_to_remove}")

    # Revert symlinks associated with this app
    # Symlinks are stored flat now, so we need to find which ones belong to this app.
    paths_to_revert = []
    for original_file_path in list(applied_file_modifications.keys()): # Iterate over copy
        # A symlink belongs to an app if its original_file_path is within the app's bundle path
        if original_file_path.startswith(app_path_to_remove + os.sep):
            paths_to_revert.append(original_file_path)
    
    reverted_count = 0
    failed_revert_count = 0
    for original_path in paths_to_revert:
        mod_info = applied_file_modifications.get(original_path)
        if not mod_info:
            continue
        
        backup_file = mod_info.get("backup_path")
        try:
            if os.path.islink(original_path):
                os.remove(original_path)
                NSLog(f"Removed symlink: {original_path}")
            elif not os.path.exists(original_path):
                NSLog(f"Original symlink path does not exist: {original_path}")
            else:
                NSLog(f"Path is not a symlink but was in records, removing file: {original_path}")
                os.remove(original_path)

            if backup_file and os.path.exists(backup_file):
                os.rename(backup_file, original_path)
                NSLog(f"Restored backup: {backup_file} to {original_path}")
            elif backup_file:
                NSLog(f"Backup file {backup_file} not found, cannot restore for {original_path}")
                # Symlink removed, but original not restored. Keep record or inform?
                messagebox.showwarning("Revert Warning", f"Backup for {os.path.basename(original_path)} not found. Symlink removed, original not restored.")
            
            del applied_file_modifications[original_path] # Remove from flat dict
            reverted_count += 1
        except Exception as e:
            NSLog(f"Error reverting symlink for {original_path} during app removal: {e}")
            messagebox.showerror("Revert Error", f"Could not revert {os.path.basename(original_path)}: {e}")
            failed_revert_count +=1

    if paths_to_revert:
        messagebox.showinfo("Symlink Reversion", f"For {app_name}:\nReverted {reverted_count} sound replacements.\nFailed to revert {failed_revert_count} (see console for details).")

    # Remove app-specific default symlink source if it exists
    if app_path_to_remove in app_default_symlink_sources:
        del app_default_symlink_sources[app_path_to_remove]
        NSLog(f"Removed default symlink source for {app_path_to_remove}")

    save_config()
    update_app_list() # Refresh notebook (removes tab)


def populate_app_tab_content(tab_frame, app_path):
    """Populates the content of a single application's tab in the notebook."""
    app_name = os.path.basename(app_path)

    # Make the tab_frame itself expand with the notebook
    tab_frame.columnconfigure(0, weight=1)
    tab_frame.rowconfigure(0, weight=1)

    # Create a canvas and a scrollbar for the tab content
    canvas = tk.Canvas(tab_frame)
    scrollbar = ttk.Scrollbar(tab_frame, orient="vertical", command=canvas.yview)
    scrollable_content_frame = ttk.Frame(canvas)

    scrollable_content_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")
        )
    )

    # Store the window ID for later use (to set its width)
    scrollable_window_id = canvas.create_window((0, 0), window=scrollable_content_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    # Function to update the scrollable_content_frame width to match the canvas width
    def _configure_canvas_window(event):
        canvas_width = event.width
        canvas.itemconfig(scrollable_window_id, width=canvas_width)
        # Also update the scrollregion when canvas size changes
        canvas.configure(scrollregion=canvas.bbox("all"))

    canvas.bind("<Configure>", _configure_canvas_window)

    # Mouse wheel scrolling for the canvas
    def _on_mousewheel(event):
        # For Windows/some macOS mice, event.delta is usually +/-120 per tick
        # For Linux/other macOS, Button-4 is scroll up, Button-5 is scroll down
        if event.num == 4: # Linux scroll up
            canvas.yview_scroll(-1, "units")
        elif event.num == 5: # Linux scroll down
            canvas.yview_scroll(1, "units")
        else: # Windows/macOS delta
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")

    # Bind mouse wheel events to the canvas and the scrollable frame within it.
    # Starting with canvas and the main scrollable frame is often sufficient.
    # canvas.bind_all("<MouseWheel>", _on_mousewheel) # For Windows and macOS with delta
    # canvas.bind_all("<Button-4>", _on_mousewheel)   # For Linux scroll up
    # canvas.bind_all("<Button-5>", _on_mousewheel)   # For Linux scroll down

    # Bind to the canvas itself
    canvas.bind("<MouseWheel>", _on_mousewheel)
    canvas.bind("<Button-4>", _on_mousewheel)
    canvas.bind("<Button-5>", _on_mousewheel)

    # Bind to the scrollable content frame as well, so scrolling works when mouse is over content
    scrollable_content_frame.bind("<MouseWheel>", _on_mousewheel)
    scrollable_content_frame.bind("<Button-4>", _on_mousewheel)
    scrollable_content_frame.bind("<Button-5>", _on_mousewheel)

    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")
    
    # All subsequent content will be placed in scrollable_content_frame
    # Configure column/row weights on the scrollable_content_frame
    scrollable_content_frame.columnconfigure(0, weight=1) # Allow content to expand horizontally
    scrollable_content_frame.columnconfigure(1, weight=0) # Define column 1 for columnspan used by header and save button

    # --- Header ---
    header_label = ttk.Label(scrollable_content_frame, text=f"Settings for: {app_name}", font=("TkDefaultFont", 14, "bold"))
    header_label.grid(row=0, column=0, columnspan=2, pady=(5,10), sticky='n')

    # --- Launch Sound Assignment ---
    launch_sound_frame = ttk.LabelFrame(scrollable_content_frame, text="Launch Sound Notification", padding=10)
    launch_sound_frame.grid(row=1, column=0, sticky='ew', padx=5, pady=5)
    launch_sound_frame.columnconfigure(0, weight=1)

    current_launch_sound = monitored_apps.get(app_path, "None")
    ttk.Label(launch_sound_frame, text="Plays when app launches:").grid(row=0, column=0, sticky='w', padx=(0,5))
    
    launch_sound_combo = ttk.Combobox(launch_sound_frame, state="readonly", width=30)
    launch_sound_combo.grid(row=1, column=0, sticky='ew', pady=(5,0))
    available_sounds_for_launch = ["None"] + sound_files
    launch_sound_combo['values'] = available_sounds_for_launch
    if current_launch_sound in available_sounds_for_launch:
        launch_sound_combo.set(current_launch_sound)
    elif available_sounds_for_launch:
        launch_sound_combo.set(available_sounds_for_launch[0])
    
    assign_launch_sound_button = ttk.Button(launch_sound_frame, text="Assign Launch Sound", 
                                          command=lambda p=app_path, c=launch_sound_combo, tf=scrollable_content_frame: assign_sound_to_app(p, c, tf))
    assign_launch_sound_button.grid(row=1, column=1, sticky='e', padx=(5,0), pady=(5,0))

    preview_launch_sound_button = ttk.Button(launch_sound_frame, text="Preview",
                                             command=lambda c=launch_sound_combo, sf=scrollable_content_frame: preview_sound(c.get(), sf))
    preview_launch_sound_button.grid(row=1, column=2, sticky='e', padx=(5,0), pady=(5,0))

    # --- Sound Replacements (Symlinks) within this App ---
    app_symlinks_frame = ttk.LabelFrame(scrollable_content_frame, text=f"Manage Sound Replacements in {app_name}", padding=10)
    app_symlinks_frame.grid(row=3, column=0, sticky='nsew', padx=5, pady=5)
    scrollable_content_frame.rowconfigure(3, weight=1) # Allow this frame to expand vertically
    scrollable_content_frame.rowconfigure(4, weight=0) # Row for remove button
    scrollable_content_frame.rowconfigure(5, weight=0) # Row for save all button
    app_symlinks_frame.columnconfigure(0, weight=1)
    app_symlinks_frame.rowconfigure(0, weight=1) # For create area
    app_symlinks_frame.rowconfigure(1, weight=1) # For active display area

    # --- Sub-frame for CREATING new symlinks (Browse app contents and list original sounds) ---
    create_symlink_frame = ttk.LabelFrame(app_symlinks_frame, text="Create New Sound Replacement", padding=5)
    create_symlink_frame.grid(row=0, column=0, sticky='nsew', pady=(0,5))
    create_symlink_frame.columnconfigure(0, weight=1)
    create_symlink_frame.rowconfigure(1, weight=1) # Allow dynamic content area to expand

    browse_app_button = ttk.Button(create_symlink_frame, text="Scan App for Sounds to Replace...",
                                   command=lambda p=app_path, frame=create_symlink_frame: browse_app_sounds_for_tab(p, frame, "dynamic_content_area")) # Pass a key
    browse_app_button.grid(row=0, column=0, sticky='w', padx=(0,5), pady=(0,5))

    # This frame will hold the list of original sounds for replacement
    symlink_creation_dynamic_content_frame = ttk.Frame(create_symlink_frame, relief="sunken", borderwidth=1)
    symlink_creation_dynamic_content_frame.grid(row=1, column=0, sticky='nsew', pady=5)
    symlink_creation_dynamic_content_frame.columnconfigure(0, weight=1)
    symlink_creation_dynamic_content_frame.rowconfigure(0, weight=1)
    # Add a placeholder to symlink_creation_dynamic_content_frame
    # This will be replaced by browse_app_sounds_for_tab
    placeholder_create_label = ttk.Label(symlink_creation_dynamic_content_frame, text="Click 'Scan App...' to find sounds to replace.", style="Placeholder.TLabel")
    placeholder_create_label.pack(padx=10, pady=10)
    create_symlink_frame.widget_refs = {'dynamic_content_area': symlink_creation_dynamic_content_frame, 'placeholder_label': placeholder_create_label}


    # --- Sub-frame for DISPLAYING ACTIVE symlinks ---
    active_symlinks_display_frame = ttk.LabelFrame(app_symlinks_frame, text="Active Sound Replacements", padding=5)
    active_symlinks_display_frame.grid(row=1, column=0, sticky='nsew', pady=5)
    active_symlinks_display_frame.columnconfigure(0, weight=1)
    active_symlinks_display_frame.rowconfigure(0, weight=1) # Allow list to expand

    # This frame will be populated by refresh_active_symlinks_for_tab
    # Initially, show a placeholder or call refresh.
    # Placeholder style is already configured.
    s = ttk.Style() # Ensure style is available
    s.configure("Placeholder.TLabel", foreground="grey")

    # Call to populate the active symlinks list
    refresh_active_symlinks_for_tab(app_path, active_symlinks_display_frame)

    # --- Remove App Button ---
    remove_specific_app_button = ttk.Button(scrollable_content_frame, text=f"Stop Monitoring {app_name} (and Revert Symlinks)", 
                                          command=lambda p=app_path: remove_selected_app(p))
    remove_specific_app_button.grid(row=4, column=0, pady=10, sticky='sew')

    # --- New Save Settings Button ---
    save_all_settings_button = ttk.Button(scrollable_content_frame, text="Save All Settings",
                                           command=save_config_and_notify) 
    save_all_settings_button.grid(row=5, column=0, columnspan=2, pady=(0,10), sticky='sew') # Span across if header label also spans


def handle_save_symlink_for_tab(row_data_dict, app_path_context, parent_widget_for_dialogs):
    """Handles creating a symlink based on data from a row in the tab UI.
    parent_widget_for_dialogs is typically content_host_frame.winfo_toplevel() (the root window).
    """
    global applied_file_modifications, root, app_notebook 

    original_path = row_data_dict['original_path']
    target_path = row_data_dict['target_path_var'].get()

    if not target_path or target_path == "<Browse for target>":
        messagebox.showwarning("Missing Target", "Please select a target sound file first using 'Browse...'.", parent=parent_widget_for_dialogs)
        return

    if not os.path.exists(target_path):
        messagebox.showerror("Error", f"Target sound file does not exist:\n{target_path}", parent=parent_widget_for_dialogs)
        return

    confirm_message = f"This will replace:\n{os.path.basename(original_path)} (within {os.path.basename(app_path_context)})\n\nwith a symlink to:\n{os.path.basename(target_path)}\n\nThe original file will be backed up.\nProceed?"
    if not messagebox.askyesno("Confirm Sound Replacement", confirm_message, parent=parent_widget_for_dialogs):
        return

    backup_path = original_path + ".bak"
    try:
        if os.path.islink(original_path):
            os.remove(original_path)
            NSLog(f"Removed existing symlink at: {original_path}")
        elif os.path.exists(original_path):
            if os.path.exists(backup_path):
                if not messagebox.askyesno("Overwrite Backup?", 
                                         f"Backup file {backup_path} already exists. Overwrite it?", 
                                         parent=parent_widget_for_dialogs):
                    return
                os.remove(backup_path)
                NSLog(f"Removed existing backup: {backup_path}")
            os.rename(original_path, backup_path)
            NSLog(f"Backed up {original_path} to {backup_path}")

        os.symlink(target_path, original_path)
        NSLog(f"Symlink created: {original_path} -> {target_path}")

        applied_file_modifications[original_path] = {
            "backup_path": backup_path,
            "target_linked_to": target_path
        }
        save_config() 
        messagebox.showinfo("Success", 
                            f"Successfully replaced sound:\n{os.path.basename(original_path)} linked to {os.path.basename(target_path)}", 
                            parent=parent_widget_for_dialogs)
        
        # Refresh the active symlinks list
        if app_notebook and root: 
            try:
                current_tab_widget_path_str = app_notebook.select()
                if not current_tab_widget_path_str: 
                    NSLog("No tab selected, cannot refresh active symlinks list.")
                    return
                
                current_tab_frame = root.nametowidget(current_tab_widget_path_str)
                current_tab_app_name = app_notebook.tab(current_tab_frame, "text")

                if os.path.basename(app_path_context) == current_tab_app_name:
                    target_refresh_frame = None
                    # Find the 'Active Sound Replacements' frame within the current tab
                    for l0_child in current_tab_frame.winfo_children(): # Usually Canvas + Scrollbar
                        if isinstance(l0_child, tk.Canvas):
                            # Get the scrollable_content_frame hosted in the canvas
                            canvas_window_items = l0_child.find_withtag("all")
                            if not canvas_window_items: continue # Should not happen if populated
                            
                            # Assuming the first item created with create_window is our scrollable frame
                            # This relies on the structure established in populate_app_tab_content
                            # A more robust way might be to store a direct reference if this proves fragile
                            canvas_window_item_path = None
                            for item_id in canvas_window_items:
                                if l0_child.type(item_id) == "window":
                                    canvas_window_item_path = l0_child.itemcget(item_id, "window")
                                    break
                            
                            if canvas_window_item_path:
                                scrollable_content_frame = root.nametowidget(canvas_window_item_path)
                                for l1_child in scrollable_content_frame.winfo_children(): # Header, LaunchSoundFrame, AppSymlinksFrame etc.
                                    if isinstance(l1_child, ttk.LabelFrame) and l1_child.cget("text").startswith(f"Manage Sound Replacements in"): 
                                        # Now inside "Manage Sound Replacements in {app_name}"
                                        for l2_child in l1_child.winfo_children(): # CreateSymlinkFrame, ActiveSymlinksDisplayFrame
                                            if isinstance(l2_child, ttk.LabelFrame) and l2_child.cget("text") == "Active Sound Replacements":
                                                target_refresh_frame = l2_child
                                                break
                                        if target_refresh_frame: break
                                if target_refresh_frame: break


                    if target_refresh_frame:
                        refresh_active_symlinks_for_tab(app_path_context, target_refresh_frame)
                    else:
                        NSLog(f"Could not find 'Active Sound Replacements' frame in tab '{current_tab_app_name}' to refresh. Manual tab switch may be needed.")
                else:
                    NSLog(f"Symlink saved for '{os.path.basename(app_path_context)}', but current tab is '{current_tab_app_name}'. Active list not refreshed.")
            except Exception as e:
                NSLog(f"Error refreshing active symlinks list in tab: {e}")
        else:
            NSLog("app_notebook or root not available for refreshing active symlinks list.")

    except Exception as e:
        messagebox.showerror("Symlink Error", f"Could not apply symlink: {e}", parent=parent_widget_for_dialogs)
        NSLog(f"Error applying symlink for tab: {e}")

def update_app_list():
    global app_notebook, monitored_apps
    if not app_notebook:
        NSLog("Notebook not initialized, cannot update app list/tabs.")
        return

    selected_tab_path = None
    try:
        if app_notebook.tabs() and app_notebook.index("current") is not None:
            current_tab_widget_path = app_notebook.select()
            current_tab_text = app_notebook.tab(app_notebook.select(), "text")
            for path in monitored_apps.keys():
                if os.path.basename(path) == current_tab_text:
                    selected_tab_path = path
                    break
    except tk.TclError: 
        pass

    for tab_id in list(app_notebook.tabs()):
        app_notebook.forget(tab_id)

    if not monitored_apps:
        empty_frame = ttk.Frame(app_notebook, padding="20")
        msg_label = ttk.Label(empty_frame, text="No applications are currently monitored. Click 'Add Monitored App' to begin.", justify=tk.CENTER, wraplength=300)
        msg_label.pack(expand=True, padx=10, pady=10)
        app_notebook.add(empty_frame, text=" Information ") 
        return

    new_selection_index = None
    tab_index = 0
    for app_path, sound_name in monitored_apps.items(): 
        app_name = os.path.basename(app_path)
        tab_frame = ttk.Frame(app_notebook, padding="10") 
        app_notebook.add(tab_frame, text=app_name, sticky="nsew")
        
        populate_app_tab_content(tab_frame, app_path)
        
        if app_path == selected_tab_path:
            new_selection_index = tab_index
        tab_index += 1

    if new_selection_index is not None:
        app_notebook.select(new_selection_index)
    elif len(app_notebook.tabs()) > 0:
        app_notebook.select(0) 


def update_sound_dropdown():
    NSLog("Global update_sound_dropdown called - this should be handled per tab now.")
    pass


def on_app_select(event):
    NSLog("on_app_select (for old listbox) called - this is obsolete.")
    pass


def get_selected_app_path():
    global app_notebook
    if not app_notebook or not app_notebook.tabs():
        return None
    try:
        selected_tab_widget_path = app_notebook.select() 
        if not selected_tab_widget_path: 
            return None

        selected_tab_text = app_notebook.tab(selected_tab_widget_path, "text")

        if selected_tab_text == " Information ": 
            return None

        for path in monitored_apps.keys():
            if os.path.basename(path) == selected_tab_text:
                return path
        NSLog(f"Could not find app_path for selected tab: {selected_tab_text}")
        return None
    except tk.TclError as e:
        NSLog(f"TclError in get_selected_app_path: {e}. Likely no actual app tab selected.")
        return None


def setup_gui(root_window):
    global app_notebook, app_listbox, sound_selection_combobox, applied_symlinks_listbox 

    root_window.title("Sound Replacer") 
    root_window.geometry("800x600") 

    outer_main_frame = ttk.Frame(root_window, padding="5")
    outer_main_frame.grid(row=0, column=0, sticky="nsew")
    root_window.columnconfigure(0, weight=1)
    root_window.rowconfigure(0, weight=1)

    top_controls_frame = ttk.Frame(outer_main_frame, padding="5")
    top_controls_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
    outer_main_frame.columnconfigure(0, weight=1) 

    add_app_button = ttk.Button(top_controls_frame, text="Add Monitored App", command=add_app)
    add_app_button.pack(side=tk.LEFT, padx=5) 

    app_notebook = ttk.Notebook(outer_main_frame)
    app_notebook.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
    outer_main_frame.rowconfigure(1, weight=1) 
    
    load_config()
    load_sound_files()
    update_app_list()  


def assign_sound_to_app(app_path, sound_combo_widget, tab_frame_parent):
    global monitored_apps 

    if not app_path or app_path not in monitored_apps:
        messagebox.showerror("Error", "Invalid application context for assigning sound.", parent=tab_frame_parent)
        return

    selected_sound = sound_combo_widget.get()
    if not selected_sound: 
        messagebox.showwarning("Selection Error", "Please select a sound from the dropdown.", parent=tab_frame_parent)
        return

    monitored_apps[app_path] = selected_sound
    save_config()
    NSLog(f"Assigned launch sound for {os.path.basename(app_path)}: {selected_sound}")
    messagebox.showinfo("Launch Sound Updated", f"Launch sound for {os.path.basename(app_path)} set to: {selected_sound}", parent=tab_frame_parent)
    update_app_list() 


def select_and_set_app_default_symlink_source(app_path, display_label_widget, tab_frame_parent):
    global app_default_symlink_sources 

    filepath = filedialog.askopenfilename(
        title=f"Select Default Symlink Source for {os.path.basename(app_path)}",
        initialdir=os.path.expanduser("~/Desktop"),
        filetypes=[("Sound files", "*.mp3 *.wav *.aiff *.m4a"), ("All files", "*.*")],
        parent=tab_frame_parent 
    )

    if filepath:
        app_default_symlink_sources[app_path] = filepath
        display_label_widget.config(text=f"Current: {filepath}")
        save_config()
        NSLog(f"Set default symlink source for {app_path} to {filepath}")
        messagebox.showinfo("Default Set", f"Default symlink source for {os.path.basename(app_path)} set.", parent=tab_frame_parent)
    else:
        NSLog(f"No file selected for default symlink source for {app_path}.")

def clear_app_default_symlink_source(app_path, display_label_widget, tab_frame_parent):
    global app_default_symlink_sources
    if app_path in app_default_symlink_sources:
        del app_default_symlink_sources[app_path]
        display_label_widget.config(text="Current: Not Set")
        save_config()
        NSLog(f"Cleared default symlink source for {app_path}")
        messagebox.showinfo("Default Cleared", f"Default symlink source for {os.path.basename(app_path)} cleared.", parent=tab_frame_parent)
    else:
        messagebox.showinfo("Info", f"No default symlink source was set for {os.path.basename(app_path)}.", parent=tab_frame_parent)


def browse_app_sounds_for_tab(app_path, create_symlink_top_frame, content_area_key):
    content_host_frame = create_symlink_top_frame.widget_refs.get(content_area_key)
    if not content_host_frame:
        NSLog(f"Error: Content area key '{content_area_key}' not found in create_symlink_top_frame refs.")
        messagebox.showerror("UI Error", "Symlink creation area not found.", parent=create_symlink_top_frame.winfo_toplevel())
        return

    for widget in content_host_frame.winfo_children():
        widget.destroy()

    sound_paths_in_app = []
    default_relative_scan_dir = os.path.join("Contents", "Resources")
    current_scan_path = os.path.join(app_path, default_relative_scan_dir)
    current_path_description_for_user = default_relative_scan_dir 
    
    initial_scan_done = False
    if os.path.isdir(current_scan_path): 
        try:
            NSLog(f"Scanning default path: {current_scan_path}")
            for dirpath, _, filenames in os.walk(current_scan_path):
                for filename in filenames:
                    if filename.lower().endswith((".wav", ".mp3", ".aiff", ".m4a")):
                        full_path = os.path.join(dirpath, filename)
                        sound_paths_in_app.append(full_path)
            initial_scan_done = True 
            NSLog(f"Found {len(sound_paths_in_app)} sounds in default path.")
        except Exception as e:
            messagebox.showerror("Error", f"Could not read app resources from '{current_path_description_for_user}': {e}", parent=content_host_frame.winfo_toplevel())
    else:
        NSLog(f"Default scan path not found or not a directory: {current_scan_path}")

    if not sound_paths_in_app: 
        prompt_title = "Scan Custom Path"
        prompt_message_intro = f"No sounds found in app's default sound location ('{default_relative_scan_dir}')."
        if not os.path.isdir(current_scan_path) and not initial_scan_done : 
             prompt_message_intro = f"App's default sound location ('{default_relative_scan_dir}') not found."

        custom_path_relative = simpledialog.askstring(
            prompt_title,
            f"{prompt_message_intro}\\n\\nEnter a path within '{os.path.basename(app_path)}' to scan (e.g., Contents/Frameworks/Some.framework/Versions/A/Resources), or leave blank to cancel:",
            parent=content_host_frame.winfo_toplevel()
        )
        if custom_path_relative:
            current_scan_path = os.path.join(app_path, custom_path_relative)
            current_path_description_for_user = custom_path_relative 
            NSLog(f"Attempting to scan custom path: {current_scan_path}")
            if os.path.isdir(current_scan_path):
                try:
                    for dirpath, _, filenames in os.walk(current_scan_path):
                        for filename in filenames:
                            if filename.lower().endswith((".wav", ".mp3", ".aiff", ".m4a")):
                                full_path = os.path.join(dirpath, filename)
                                sound_paths_in_app.append(full_path)
                    NSLog(f"Found {len(sound_paths_in_app)} sounds in custom path '{current_path_description_for_user}'.")
                except Exception as e:
                    messagebox.showerror("Error", f"Could not read from custom path '{current_path_description_for_user}': {e}", parent=content_host_frame.winfo_toplevel())
                    ttk.Label(content_host_frame, text=f"Error scanning custom path '{current_path_description_for_user}'. No sounds listed.", style="Placeholder.TLabel").pack(padx=10, pady=10)
                    return
            else:
                messagebox.showerror("Invalid Path", f"The custom path '{current_path_description_for_user}' (resolved to '{current_scan_path}') is not a valid directory.", parent=content_host_frame.winfo_toplevel())
                ttk.Label(content_host_frame, text=f"Custom path '{current_path_description_for_user}' invalid. No sounds listed.", style="Placeholder.TLabel").pack(padx=10, pady=10)
                return
        else: 
            NSLog("User cancelled custom path input or provided no input.")
            placeholder_msg = f"No sounds found. Scan of '{default_relative_scan_dir}' was empty, and no custom path was provided."
            if not initial_scan_done: 
                placeholder_msg = f"App's default sound location ('{default_relative_scan_dir}') not found, and no custom path was provided."
            
            placeholder_widget = create_symlink_top_frame.widget_refs.get('placeholder_label')
            if placeholder_widget:
                placeholder_widget.config(text=placeholder_msg)
                placeholder_widget.pack(padx=10, pady=10) 
            else: 
                ttk.Label(content_host_frame, text=placeholder_msg, style="Placeholder.TLabel").pack(padx=10, pady=10)
            return

    if not sound_paths_in_app:
        final_msg = f"No common sound files found in '{os.path.basename(app_path)}' using path '{current_path_description_for_user}' (and its subfolders)."
        NSLog(final_msg)
        messagebox.showinfo("No Sounds Found", final_msg, parent=content_host_frame.winfo_toplevel())
        
        placeholder_widget = create_symlink_top_frame.widget_refs.get('placeholder_label')
        if placeholder_widget:
            placeholder_widget.config(text=f"No sound files found in '{current_path_description_for_user}'.")
            placeholder_widget.pack(padx=10, pady=10)
        else:
            ttk.Label(content_host_frame, text=f"No sound files found in '{current_path_description_for_user}'.", style="Placeholder.TLabel").pack(padx=10, pady=10)
        return

    NSLog(f"Proceeding to display {len(sound_paths_in_app)} found sound(s) from '{current_path_description_for_user}'.")

    list_header_frame = ttk.Frame(content_host_frame)
    list_header_frame.pack(fill=tk.X, pady=(5,2))
    ttk.Label(list_header_frame, text="App Sound File (Original)", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, padx=2, sticky='w')
    ttk.Label(list_header_frame, text="Preview", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=1, padx=2, sticky='w') 
    ttk.Label(list_header_frame, text=" -> ", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=2, padx=2, sticky='w') 
    ttk.Label(list_header_frame, text="Your Sound (Target)", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=3, columnspan=2, padx=2, sticky='w') 
    ttk.Label(list_header_frame, text="Actions", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=5, columnspan=2, padx=2, sticky='w') 

    list_header_frame.columnconfigure(0, weight=3) 
    list_header_frame.columnconfigure(1, weight=1) 
    list_header_frame.columnconfigure(2, weight=0) 
    list_header_frame.columnconfigure(3, weight=1) 
    list_header_frame.columnconfigure(4, weight=2) 
    list_header_frame.columnconfigure(5, weight=1) 
    list_header_frame.columnconfigure(6, weight=1) 

    canvas = tk.Canvas(content_host_frame)
    scrollbar = ttk.Scrollbar(content_host_frame, orient="vertical", command=canvas.yview)
    scrollable_frame = ttk.Frame(canvas)

    scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    
    canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    
    def _on_sound_list_canvas_configure(event):
        canvas.itemconfig(canvas_window, width=event.width)
        canvas.configure(scrollregion=canvas.bbox("all")) 
    canvas.bind("<Configure>", _on_sound_list_canvas_configure)

    def _on_mousewheel_sound_list(event):
        scroll_val = 0
        if event.num == 4: 
            scroll_val = -1
        elif event.num == 5: 
            scroll_val = 1
        elif event.delta: 
            scroll_val = int(-1*(event.delta/120))
        
        if scroll_val != 0:
            canvas.yview_scroll(scroll_val, "units")
            
    canvas.bind("<MouseWheel>", _on_mousewheel_sound_list)
    scrollable_frame.bind("<MouseWheel>", _on_mousewheel_sound_list) 
    canvas.bind("<Button-4>", _on_mousewheel_sound_list)
    canvas.bind("<Button-5>", _on_mousewheel_sound_list)
    scrollable_frame.bind("<Button-4>", _on_mousewheel_sound_list)
    scrollable_frame.bind("<Button-5>", _on_mousewheel_sound_list)

    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    current_symlink_options = [] 
    default_target_sound_for_app = app_default_symlink_sources.get(app_path, "")

    for i, original_path_candidate in enumerate(sound_paths_in_app):
        row_data = {
            'original_path': original_path_candidate,
            'target_path_var': tk.StringVar(value=default_target_sound_for_app if default_target_sound_for_app else "<Browse for target>"),
            'target_display_label': None
        }

        row_frame = ttk.Frame(scrollable_frame)
        row_frame.pack(fill=tk.X, pady=1, padx=1)
        row_frame.columnconfigure(0, weight=3) 
        row_frame.columnconfigure(1, weight=1) 
        row_frame.columnconfigure(2, weight=0) 
        row_frame.columnconfigure(3, weight=1) 
        row_frame.columnconfigure(4, weight=2) 
        row_frame.columnconfigure(5, weight=1) 
        row_frame.columnconfigure(6, weight=1) 

        rel_original_path = os.path.relpath(original_path_candidate, start=app_path)
        ttk.Label(row_frame, text=rel_original_path, wraplength=250, anchor="w").grid(row=0, column=0, sticky="ew", padx=(0,2))

        preview_original_button = ttk.Button(row_frame, text="Preview Original", width=15,
                                           command=lambda orig_path=original_path_candidate, p_widget=content_host_frame: preview_sound(orig_path, p_widget.winfo_toplevel()))
        preview_original_button.grid(row=0, column=1, sticky="ew", padx=2)

        ttk.Label(row_frame, text="->").grid(row=0, column=2, padx=2)
        
        select_target_button = ttk.Button(row_frame, text="Browse...", width=10,
                                          command=lambda r_data=row_data: select_target_for_symlink_row(r_data, content_host_frame.winfo_toplevel()))
        select_target_button.grid(row=0, column=3, sticky="ew", padx=2)

        row_data['target_display_label'] = ttk.Label(row_frame, textvariable=row_data['target_path_var'], wraplength=200, anchor="w")
        row_data['target_display_label'].grid(row=0, column=4, sticky="ew", padx=2)
        
        save_button = ttk.Button(row_frame, text="Replace", width=10,
                                 command=lambda r_data=row_data: handle_save_symlink_for_tab(r_data, app_path, content_host_frame.winfo_toplevel()))
        save_button.grid(row=0, column=5, sticky="ew", padx=2)

        preview_target_button = ttk.Button(row_frame, text="Preview Target", width=15, 
                                           command=lambda r_data=row_data, p_widget=content_host_frame: preview_sound(r_data['target_path_var'].get(), p_widget.winfo_toplevel()))
        preview_target_button.grid(row=0, column=6, sticky="ew", padx=2)
        
        current_symlink_options.append(row_data)
    
    content_host_frame.update_idletasks()
    canvas.configure(scrollregion=canvas.bbox("all"))


def select_target_for_symlink_row(row_data_dict, parent_widget):
    desktop_path = os.path.expanduser("~/Desktop")
    filepath = filedialog.askopenfilename(
        title="Select Target Sound File to Link From",
        initialdir=desktop_path,
        filetypes=[("Sound files", "*.mp3 *.wav *.aiff *.m4a"), ("All files", "*.*")],
        parent=parent_widget
    )
    if filepath:
        row_data_dict['target_path_var'].set(filepath)


def refresh_active_symlinks_for_tab(app_path, target_frame):
    global applied_file_modifications
    for widget in target_frame.winfo_children():
        widget.destroy()

    active_symlinks_for_this_app = []
    for original_file, mod_info in applied_file_modifications.items():
        normalized_app_path = os.path.normpath(app_path)
        normalized_original_file = os.path.normpath(original_file)
        if normalized_original_file.startswith(normalized_app_path + os.sep):
            active_symlinks_for_this_app.append({
                'original_path': original_file,
                'target_linked_to': mod_info.get('target_linked_to', '<Unknown Target>'),
                'backup_path': mod_info.get('backup_path', '<No Backup Info>')
            })

    if not active_symlinks_for_this_app:
        ttk.Label(target_frame, text="No active sound replacements (symlinks) for this app.", style="Placeholder.TLabel").pack(padx=10, pady=10)
        return

    header_frame = ttk.Frame(target_frame)
    header_frame.pack(fill=tk.X, pady=(5,2))
    ttk.Label(header_frame, text="Original App Sound (Replaced)", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, padx=2, sticky='w')
    ttk.Label(header_frame, text="Currently Linked To", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=1, padx=2, sticky='w')
    ttk.Label(header_frame, text="Action", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=2, padx=2, sticky='w')
    header_frame.columnconfigure(0, weight=2)
    header_frame.columnconfigure(1, weight=2)
    header_frame.columnconfigure(2, weight=1)

    list_frame = ttk.Frame(target_frame)
    list_frame.pack(fill=tk.BOTH, expand=True)

    for symlink_info in active_symlinks_for_this_app:
        row_frame = ttk.Frame(list_frame)
        row_frame.pack(fill=tk.X, pady=1, padx=1)
        row_frame.columnconfigure(0, weight=2)
        row_frame.columnconfigure(1, weight=2)
        row_frame.columnconfigure(2, weight=1)

        rel_original_path = os.path.relpath(symlink_info['original_path'], start=app_path)
        ttk.Label(row_frame, text=rel_original_path, wraplength=250, anchor="w").grid(row=0, column=0, sticky="ew", padx=(0,2))
        
        target_basename = os.path.basename(symlink_info['target_linked_to'])
        ttk.Label(row_frame, text=target_basename, wraplength=200, anchor="w").grid(row=0, column=1, sticky="ew", padx=2)

        revert_button = ttk.Button(row_frame, text="Revert", width=10,
                                   command=lambda op=symlink_info['original_path'], ap=app_path, tf=target_frame: \
                                       revert_selected_symlink(op, ap, tf.winfo_toplevel(), tf))
        revert_button.grid(row=0, column=2, sticky="ew", padx=2)

        preview_active_target_button = ttk.Button(row_frame, text="Preview Target", width=15,
                                                command=lambda path=symlink_info['target_linked_to'], p_widget=target_frame: preview_sound(path, p_widget.winfo_toplevel()))
        preview_active_target_button.grid(row=0, column=3, sticky="ew", padx=2)

def revert_selected_symlink(original_path_to_revert, app_path_context, parent_widget_for_dialogs, active_symlinks_list_frame_to_refresh):
    global applied_file_modifications

    if not messagebox.askyesno("Confirm Revert", 
                               f"Are you sure you want to revert the sound replacement for:\\n{os.path.basename(original_path_to_revert)}\\n\\nThis will attempt to restore the original file from its backup.",
                               parent=parent_widget_for_dialogs):
        return

    mod_info = applied_file_modifications.get(original_path_to_revert)
    if not mod_info:
        messagebox.showerror("Error", "Symlink information not found in records. Cannot revert.", parent=parent_widget_for_dialogs)
        NSLog(f"Attempted to revert {original_path_to_revert}, but no record found.")
        return

    backup_file = mod_info.get("backup_path")
    reverted_successfully = False
    try:
        if os.path.islink(original_path_to_revert):
            os.remove(original_path_to_revert)
            NSLog(f"Removed symlink: {original_path_to_revert}")
        elif not os.path.exists(original_path_to_revert):
            NSLog(f"Original symlink path did not exist: {original_path_to_revert}. No symlink to remove.")
        else:
            NSLog(f"Path {original_path_to_revert} exists but is not a symlink. Proceeding to restore backup if possible.")

        if backup_file and os.path.exists(backup_file):
            os.rename(backup_file, original_path_to_revert)
            NSLog(f"Restored backup: {backup_file} to {original_path_to_revert}")
            reverted_successfully = True
        elif backup_file:
            messagebox.showwarning("Revert Warning", f"Backup file '{os.path.basename(backup_file)}' not found. Symlink (if any) removed, but original could not be restored.", parent=parent_widget_for_dialogs)
            NSLog(f"Backup file {backup_file} not found for {original_path_to_revert}.")
            if not os.path.islink(original_path_to_revert) and not os.path.exists(original_path_to_revert):
                 reverted_successfully = True 
        else: 
            messagebox.showwarning("Revert Warning", f"No backup information found for {os.path.basename(original_path_to_revert)}. Symlink (if any) removed, original not restored.", parent=parent_widget_for_dialogs)
            NSLog(f"No backup path recorded for {original_path_to_revert}.")
            if not os.path.islink(original_path_to_revert) and not os.path.exists(original_path_to_revert):
                 reverted_successfully = True

        if reverted_successfully:
            del applied_file_modifications[original_path_to_revert]
            save_config()
            messagebox.showinfo("Revert Successful", f"Successfully reverted sound replacement for {os.path.basename(original_path_to_revert)}.", parent=parent_widget_for_dialogs)
            NSLog(f"Reverted and removed record for {original_path_to_revert}")
        else:
            messagebox.showerror("Revert Issue", f"Could not fully revert {os.path.basename(original_path_to_revert)}. See console for details.", parent=parent_widget_for_dialogs)

    except Exception as e:
        messagebox.showerror("Revert Error", f"Error reverting {os.path.basename(original_path_to_revert)}: {e}", parent=parent_widget_for_dialogs)
        NSLog(f"Exception during revert of {original_path_to_revert}: {e}")

    refresh_active_symlinks_for_tab(app_path_context, active_symlinks_list_frame_to_refresh)

if __name__ == "__main__":
    global root 
    print(f"Tkinter version: {tk.TkVersion}") 
    if not os.path.exists(SOUNDS_DIR):
        os.makedirs(SOUNDS_DIR)
        print(f"'{SOUNDS_DIR}' directory created. Please add sound files there.")

    monitor_thread = threading.Thread(target=start_app_monitoring, daemon=True)
    monitor_thread.start()

    root = tk.Tk()
    setup_gui(root)

    def on_closing():
        save_config() 
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop() 