This app bundles the Telegram Uploader Python script.

Run the app. The launcher calls the system's `python3` to execute the bundled
script at Resources/telegram_uploader_gui_python.py. For best results, install
and use a virtual environment and ensure python3 has the required packages (requests).

If you need the app to use a contained Python runtime, consider packaging with
py2app or creating a custom runtime in Resources and adjusting the launcher.
