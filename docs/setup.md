# Setup and configuration



[Return to the project README](../README.md)



## Run



Use `python start_demo.py` for evaluation. Keep `.resolv-local.db` and

`.resolv-local-password` together. If they disagree, the launcher stops and

explains how to recover. Run `python start_demo.py --reset-demo-password`

to reset only the six original demo users to the launcher password. This

keeps tickets, evidence and configuration. Use this command only on the

local demo database.



For a normal installation, set `RESOLV_ADMIN_PASSWORD` (12 to 256 characters)

before running `python server.py`. Open http://127.0.0.1:8000 and sign in

as `administrator` or the ID set by `RESOLV_ADMIN_ID`. Create technicians

in **Users**. If an administrator already exists, the bootstrap variable

does not reset its password. A server without an administrator prints

bootstrap instructions.



To seed a local demo manually, set `RESOLV_DEMO_PASSWORD` and use an empty

database with a loopback listener. If an older database still contains

the shipped demo password, supply a new demo password on loopback before

starting. A database whose passwords were already changed by an older

build needs an explicit demo reset or administrator-approved account recovery.



New IDs allow 2 to 20 letters, digits, periods, underscores or hyphens.

Sign-in trims surrounding spaces and accepts a case-insensitive ID when

it is unique. Existing case-variant IDs keep their exact sign-in spelling.

New and recovered passwords require 12 to 256 characters. Existing

passwords remain usable unless explicitly reset. Failed sign-ins are

limited per address and per account/address pair. Server restarts end

sessions. The browser returns users to sign-in when the session expires.



Run `python3 resolv.py` for the console workflows. See the [18-minute demo plan](demo.md) for the presentation.



## Bring an existing installation over

Resolv uses the `RESOLV_` environment variable prefix. Update your launcher,
deployment and SMTP configuration to match the names in this guide. To keep an
existing installation, point `RESOLV_DATABASE` and `RESOLV_ATTACHMENTS` at the
same database and evidence directory. For the local demo launcher, copy the
existing database, generated password file and evidence directory beside
`start_demo.py` as `.resolv-local.db`, `.resolv-local-password` and
`.resolv-local-evidence`. Stop the prior instance and back up its data before
copying. For Docker, use the same Compose project name to reuse its named volume and
set `RESOLV_DATABASE` to the existing database file inside `/data` before
starting the renamed project.

## Tests

Run the original end-to-end tests against a fresh demo database. Set `RESOLV_DATABASE` to a different fresh database for each original suite and `RESOLV_PORT` to a free local port if the app is already running. The enhancement suites create isolated servers and databases automatically. The browser test requires Playwright and Chromium (`python -m pip install -r requirements-test.txt` and `python -m playwright install chromium`). Set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` to use an existing Chromium executable.



```sh

python3 -m unittest                 # Core and enhancement unit/integration tests

python3 tests_e2e/e2e_api.py        # Core REST workflows and restart persistence

python3 tests_e2e/e2e_login_api.py  # Sign-in, throttling and duplicate handling

python3 tests_e2e/e2e_ui.py         # Core browser workflows

python3 tests_e2e/e2e_enhancements_api.py

python3 tests_e2e/e2e_recommendations_api.py

python3 tests_e2e/e2e_followup_api.py

python3 tests_e2e/e2e_followup_ui.py

python3 tests_e2e/e2e_enhancements_ui.py  # Set RESOLV_VOSK_MODEL for the recording/transcription check

```



## Runtime configuration



The server binds to loopback by default. Set `RESOLV_HOST` and `RESOLV_PORT` to override the bind address and port. `RESOLV_DATABASE` and `RESOLV_ATTACHMENTS` specify the SQLite file and private attachment directory. Keep those together for backup and recovery. Startup migrates existing SQLite audit rows in place, preserving their IDs and content. Scheduled SLA checks run every 30 seconds by default. Set `RESOLV_MONITOR_SECONDS=0` to use manual checks only. Set `RESOLV_CORS_ORIGINS` to a comma-separated list only when a cross-origin client is required. Set `RESOLV_TRUSTED_PROXIES` to the exact addresses of proxies that sanitize forwarding headers. Set `RESOLV_HTTPS_ONLY=1` behind a TLS-only endpoint to send HSTS.



### SMTP



The application uses simulated email when `RESOLV_SMTP_HOST` is unset. Set the following environment variables to enable the real SMTP adapter. Keep environment files and credentials outside version control.



| Variable | Value |

|---|---|

| `RESOLV_SMTP_HOST`, `RESOLV_SMTP_PORT` | SMTP endpoint. Default port is 587 for STARTTLS or 465 for implicit TLS. |

| `RESOLV_SMTP_FROM` | Sender address required by the provider. |

| `RESOLV_SMTP_TLS` | `starttls` (default), `ssl`, or `none` for a local test server. |

| `RESOLV_SMTP_USER`, `RESOLV_SMTP_PASSWORD` | Provider credentials when authentication is required. |

| `RESOLV_SMTP_RECIPIENTS` | JSON object mapping Resolv user IDs to email addresses. |

| `RESOLV_SMTP_SUPPORT` | Optional support address for overrides, SLA warnings and breaches, and escalation. |

| `RESOLV_SMTP_EVENTS` | Optional comma-separated event codes from `mailer.py`. Defaults to all supported events. |

| `RESOLV_BASE_URL` | Optional application URL used to create incident links. |



The administrator's **Admin → Email delivery** panel shows delivery state, attempt counts, and diagnostic codes. Temporary failures retry a maximum of three total attempts. SLA events are generated using **Run SLA monitor now** in Analytics.



### Local voice transcription



Install `vosk==0.3.45` and make FFmpeg available on `PATH`. Download and extract an English Vosk model, such as [vosk-model-small-en-us-0.15](https://alphacephei.com/vosk/models). Set `RESOLV_VOSK_MODEL` to the extracted directory and start the server. Microphone recording requires loopback or HTTPS. The browser sends audio to the app's local server for processing in memory. The transcript stays editable and the reporter submits the issue explicitly. The optional **Dictate description** control uses browser speech recognition when supported and asks before sharing audio with the browser provider.



### Evidence limits and campus map



`RESOLV_FILE_COUNT` defaults to five files per issue. `RESOLV_FILE_BYTES` defaults to 10 MiB per file. Supported formats are JPEG, PNG, WebP, PDF, DOCX and UTF-8 text. Authorized users can add evidence until an incident is closed.



The campus map contains 54 selectable areas, including all 12 hostels. Marker coordinates use the [official SWD campus directory](https://swd.bits-hyderabad.ac.in/app/campus-map) and its published map links, with building and path geometry from [OpenStreetMap contributors](https://www.openstreetmap.org/copyright). Offices sharing C Block use the same published coordinates, displayed with separate callouts. Connaught Place and Faculty Quarters use representative anchors from the official directory. Campus staff should confirm exact placements before relying on the map for navigation. The [official BITS colour emblem](https://www.bits-pilani.ac.in/wp-content/uploads/image-44.png) is stored locally.



## Follow-up features



- **My analytics** remains available to employees and shows their own incidents. **Export CSV** is available to administrators, technicians, team leads and IT managers within their incident scope. CSV export includes active filters and escapes spreadsheet formulas. Import CSV as UTF-8 in Excel when opening it directly does not preserve non-ASCII text.

- **Account requests** is an administrator queue for identity-verified access and password recovery. Approved codes expire after 15 minutes, can be used once, and are shown once to the approving administrator. Set `RESOLV_OPEN_REGISTRATION=0` to require approval for new accounts.

- Create **TEAM_LEAD** and **IT_MANAGER** accounts in the user directory to route the corresponding escalation tiers. Both see incidents that have reached their tier. The SLA history records each warning, breach, and escalation, including the actor for manual escalations.

- **Admin** lets operators choose priority and assignment strategies, edit category weights and relationship thresholds, and set SLA working hours. Existing databases keep the historical priority strategy unless an administrator changes it. New databases default to the brief formula.

- **Triage assistant** uses local rules by default. A deployment may set `RESOLV_GEMINI_API_KEY` for optional Gemini suggestions. The user explicitly opts in to sending the conversation and category names. No previous incident content is included in provider requests.



See [Follow-up implementation and architecture](followup.md) and the [test catalogue](test-catalog.md).



## Container setup



With a strong `RESOLV_ADMIN_PASSWORD` provided in the environment, run `docker compose up --build -d` and open http://127.0.0.1:8000. `RESOLV_CONTAINER_PORT` can change the host port. Compose keeps the SQLite database and evidence in its named volume. Use a single desk container for a given database. The base image supports the web app and browser dictation. Local Vosk transcription requires FFmpeg, the optional Python package and a model as described above.



## Time and demonstration



SLA working hours, holiday dates and analytics day/hour grouping follow the

server's local time. Keep the server time zone consistent with campus time

and use synchronized clocks. Account code expiry uses elapsed real time.

The demo clock affects incident operations, while account and session

expiry use real time.



A same-reporter duplicate links back to the existing incident. Open it

and use **Reply / add info** to add details. Reports by different people

can be reviewed as related records in **Insights**.

