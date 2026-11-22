Resolv — Campus Service Desk
============================

This repository contains the current application source, web assets,
SQLite migrations, Docker configuration, tests, diagrams, and project
reports. README.md has the detailed configuration and feature guide.

Run a local demo
----------------
1. Install Python 3.12 or newer.
2. Open a terminal in the folder containing this file.
3. Run: python start_demo.py
   On macOS or Linux, use python3 if that is your Python command.
4. Open http://127.0.0.1:8765/ if the browser does not open automatically.
5. Sign in as A1 (administrator), E1 (employee), or T1 (technician).
   The launcher prints the locally generated demo password in the terminal.

If the demo password and database disagree, run
python start_demo.py --reset-demo-password to reset the six demo accounts.

Keep the terminal open while using the app. Press Ctrl+C to stop it.
Use python start_demo.py --port 8766 if port 8765 is already in use.

Install optional dependencies
-----------------------------
The core app uses Python's standard library and SQLite.

    python -m pip install -r requirements.txt

For browser tests:

    python -m pip install -r requirements-test.txt
    python -m playwright install chromium

For local voice transcription, install FFmpeg and an English Vosk model
alongside the Python package listed in requirements-voice.txt. Configure
RESOLV_VOSK_MODEL and start server.py as described in README.md.

Verify the application
----------------------
    python -m unittest
    python tests_e2e/e2e_followup_api.py
    python tests_e2e/e2e_followup_ui.py

Run the full suite using the commands in README.md. A configured SMTP
server and Gemini API key are optional. The demo uses simulated email
and local triage rules by default.

Upload to GitHub
----------------
Extract the archive first. In your repository, choose Add file, then
Upload files, and upload the extracted files and folders, including
.gitignore and .dockerignore. Keep this file and README.md at the root.

Use the archive contents for the upload before running the local demo,
or use Git so .gitignore excludes the generated database, evidence and
password file. For deployment settings and Docker Compose, see README.md.
