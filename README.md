# Air780E SMS Bridge

> English | **[简体中文](README.zh-CN.md)**

An SMS receive / forward bridge built on the **Luat Air780E (4G Cat.1) module**. It talks to the module over a **USB serial port using AT commands** (USB ID `19d1:0001`, BYD EigenComm Compo) and exposes a **REST API** plus a **web management page**: incoming SMS are archived to storage automatically and forwarded to DingTalk / WeCom / Feishu / Telegram / Email / Webhook; it also supports sending SMS and scheduled SMS tasks.

> This project only uses SMS-related AT commands (`AT+CMGF` / `AT+CMGS` / `AT+CMGL` / `AT+CMGD` / `AT+CNMI` / `AT+CLIP`, etc.). **PPP/NCM data dial-up is never enabled, so it consumes no SIM data traffic.**

![Screenshot_1](docs/img/Screenshot_1.png)

## Highlights

- **Receive SMS**: poll `AT+CMGL` (no argument = REC UNREAD) → de-duplicate → store in SQLite (WAL) → forward per config → delete one by one; nothing is kept in module/SIM storage
- **Send SMS**: queued via REST, sent serially in the background, auto-switches between GSM 7-bit TEXT mode (160 chars/message) and PDU-UCS2 (67 chars/message) for Chinese/non-ASCII; content that spans several messages is sent as ONE concatenated (UDHI) long SMS so the recipient gets a single reassembled text
- **Notification forwarding**: DingTalk / WeCom (group bot) / Feishu / Telegram / Email, all delivered through **Apprise** underneath; a dedicated **Webhook HTTP push** and arbitrary **Apprise URL** channels are also available
- **Scheduled tasks**: send SMS on a daily interval (`interval_days`), with manual instant trigger
- **Contacts**: map numbers ↔ display names, shown directly in the conversation view
- **SMS gateway**: send and query SMS through the REST API to act as your own gateway
- **Self-healing**: auto-reconnect on disconnect; a command lock + single reader thread avoid clashes with polling
- **Frontend**: static single-page app (status / messages / conversations / notifications / keys / tasks / contacts / logs), JWT login with password change
- **API**: RESTful with Bearer auth (admin JWT or API key); keys can be created/revoked (with last-used timestamp)

## Quick Start

### Docker

```bash
# Pull the image directly
docker pull ghcr.io/znhocn/air780e-bridge:latest

# Run (map the device serial ports into the container)
docker run -d --name air780e-bridge --restart unless-stopped \
  --device /dev/ttyACM0:/dev/ttyACM0 \
  --device /dev/ttyACM1:/dev/ttyACM1 \
  --device /dev/ttyACM2:/dev/ttyACM2 \
  -p 8000:8000 \
  -v $(pwd)/data:/data \
  ghcr.io/znhocn/air780e-bridge:latest
```

### Docker Compose (recommended)

`docker-compose.yml` embeds `TZ: Asia/Shanghai`, maps host port **8000** → container **8000**, and mounts a data volume `data:/data`; everything else uses sensible defaults out of the box.

```bash
# Create a directory
mkdir air780e-bridge && cd air780e-bridge/

# Download docker-compose.yml
wget https://github.com/znhocn/air780e-bridge/raw/refs/heads/main/docker-compose.yml

# Run
docker compose up -d
```

### First-time Deployment

Open `http://<host>:8000`:
- When no admin exists yet, the page guides you to **create an admin account** (username + password of at least 8 characters); you are logged in automatically after success;
- Afterwards, log in with that account; the management UI calls the API with its JWT.

### Hardware Setup (Serial Port & SIM)

1. Plug the Air780E serial board into the host via USB and confirm enumeration:

   ```bash
   lsusb   # Bus ... ID 19d1:0001 BYD EigenComm Compo
   ls /dev/ttyACM*
   ```

   The board enumerates as 3 CDC-ACM ports (`ttyACM0/1/2`). `ttyACM0` and `ttyACM2` both work as AT ports; `ttyACM1` is the data/diagnostic port.

2. Install the udev rules (ordinary-user access + mark the primary AT port, preventing ModemManager from grabbing it):

   ```bash
   sudo udevadm control --reload && sudo udevadm trigger
   sudo usermod -aG dialout $USER   # effective after re-login
   ```

3. Insert the SIM card and verify quickly from the CLI:

   ```bash
   .venv/bin/python -m app.cli status    # expect +CREG: 0,1 (or 5) and +CSQ other than 99
   .venv/bin/python -m app.cli probe     # probe each serial port for an AT response
   ```

## Usage (Management Page)

- **Status**: connection / serial port / IMEI / ICCID / SIM / operator / signal (`CSQ` and dBm) / registration state, plus today's in/out/failed message counts and incoming-call stats
- **Messages**: full in/out history (including the raw AT response `raw`), per-number conversation view (contact names shown automatically), with search / pagination / scroll-up loading
- **Send**: type in a number and content to send instantly; status updates live as `queued → sending → sent/failed`
- **Notification settings**: configure forwarding channels and match filters one by one, with a "test" push anytime
- **API keys**: create / revoke / restore keys and view last-used time (the plaintext key is shown only once at creation)
- **Scheduled tasks**: periodic sends (days), with manual instant trigger
- **Contacts**: number ↔ name ↔ note
- **Logs**: `/api/forward-logs` shown in the UI (channel, config name, masked target, success/failure, error message)
- **About**: the version number is shown at the bottom-left of the page (same source as `/api/version`)

## REST API

See the [API documentation](docs/API.md) (Chinese).

### Send Example (test against 10086)

```bash
curl -X POST http://localhost:8000/api/messages/send \
  -H "Authorization: Bearer <API_KEY>" -H "Content-Type: application/json" \
  -d '{"to":"10086","content":"hello"}'
```

Sending is an async queue: the endpoint returns `{ "id": …, "status": "queued" }` and the background worker actually sends it; the status later becomes `sent` / `failed` (the `raw` field holds the AT response, e.g. `+CMS ERROR: 331` means no network service / no SIM).

## Notification Channels

`type` ∈ `dingtalk` / `wecom` / `feishu` / `telegram` / `email` / `webhook` / `apprise`. Required/optional `params` fields (the form is rendered dynamically in the "Notification settings" page):

| type | required | optional |
|---|---|---|
| `dingtalk` | `token` | `secret` (signing), `phone` |
| `wecom` | `botkey` (key or full webhook URL) | — |
| `feishu` | `token` (token or full webhook URL) | — |
| `telegram` | `bot_token`, `chat_id` (may contain `@`) | — |
| `email` | `smtp_host`, `user`, `password`, `from`, `to` | `smtp_port` (default 587), `mode` (`starttls`/`ssl`) |
| `webhook` | `url` | — |
| `apprise` | `url` (full URL of any supported service, e.g. `tgram://`, `slack://`) | — |

- Filtering: `match_from` (sender contains substring), `match_contains` (content contains substring); empty = no restriction, notifications fire only on a match
- DingTalk / WeCom / Feishu / Telegram / Email are delivered by Apprise (URL structure is validated before saving; the internal URL is not exposed in the UI); Webhook is a standalone HTTP POST (2xx counts as success)
- Both successful and failed pushes are written to `forward_logs` (channel, config name, masked target, result, error)

Webhook push body:

```json
{ "event": "sms", "direction": "in", "sender": "10086", "receiver": "",
  "content": "Your balance is 50 yuan", "received_at": "2026-09-20 00:15:45" }
```

`event` is `sms` (SMS) or `call` (incoming call, body fixed to `Incoming call`).

## FAQ

- **`+CREG: 0,0` can't register / `+CMS ERROR: 331`**: no SIM, loose SIM, or no signal → check `AT+CPIN?` (should be `+CPIN: READY`; `+CME ERROR: 10` means no card inserted).
- **Cannot open the serial port / ModemManager grabs it**: install the udev rules; if necessary `systemctl stop ModemManager`.
- **Both ports respond to AT**: `ttyACM0` and `ttyACM2` both work; pin `DEVICE_PORT` to one of them and don't let another process hold the others.
- **No SMS received**: confirm `AT+CMGF=1`, `AT+CNMI=2,1,0,0,0`, and that the SIM can receive SMS normally; on the receive side `AT+CMGL` (no argument = REC UNREAD) is polled every `POLL_INTERVAL` seconds. The Air780E does not support numeric enumeration such as `AT+CMGL=4`.
- **No data consumption**: the project never issues `AT+CGDATA` / `AT+CGACT` or PPP — it only uses SMS AT commands.
- **Forgot admin password**: edit SQLite directly with `UPDATE admins SET password_hash='…', salt='…' WHERE username='…'` (PBKDF2-SHA256, 240000 rounds; generate with `python -c "import hashlib;print(hashlib.pbkdf2_hmac('sha256',b'new_password',bytes.fromhex('salt'),240000).hex())"`).

## Technical Notes

- **Receiving**: poll `AT+CMGL` (no argument = REC UNREAD; the Air780E rejects `AT+CMGL=4` with `+CMS ERROR: 500`) **in PDU mode** every `POLL_INTERVAL` → parse SMS-DELIVER/SUBMIT PDUs (the module strips the leading SCA octet) → **long-SMS assembly by the real segment sequence number**: every part's `05 00 03 <ref> <total> <seq>` connection header is preserved in the PDU, so parts are grouped by `(sender, ref, total)` and joined by `seq` — true network order even when pages arrive out of sequence (a TEXT-mode, SIM-index join would scramble them) → store as `stored` → push once → delete per segment → restore `AT+CMGF=1`, then clean up leftovers *only if a fresh pre-purge re-list is empty* (so an SMS that just arrived is never wiped from the SIM). Incomplete concatenation groups (a segment still missing) are not stored and are left in the SIM to be completed on the next poll; each partial group is tracked by `(sender, ref, total)` and, if still missing a segment after 24 hours, is explicitly deleted by its SIM index so a broken long SMS can never permanently wedge SIM storage. The pre-STORE residue purge only defers when a truly *new* unread arrival is detected in the re-list (so it never wipes a just-arrived SMS); unread segments of a known-incomplete group no longer block the cleanup, since `AT+CMGD=1,2` skips unread entries anyway. Incoming senders are normalized to digits; notifications and `on_message` run on a dedicated thread so a slow webhook can never stall AT reads/polling; `forward_logs` older than 180 days are auto-pruned every 8 hours. Messages and logs live only in SQLite; nothing is kept in module/SIM storage.
- **Sending**: single-segment pure-ASCII goes through TEXT mode (`CSCS="GSM"` + `CSMP=17,167,0,0`, 160 chars/message); single-segment Chinese goes through PDU mode (DCS=8 UCS2, 67 chars). **Multi-segment content is sent as one concatenated long SMS in PDU mode** (`AT+CMGF=0`, one `AT+CMGS` per segment with a `05 00 03 <ref> <total> <seq>` User Data Header, then TEXT mode restored): ASCII uses DCS=0 GSM-7bit (153 chars/segment), Chinese uses DCS=8 UCS2 (67 chars/segment) — the recipient reassembles everything into a single message. PDU is mandatory for Chinese: on the Air780E the TEXT-mode `AT+CMGS` transmits the payload verbatim (never hex-decodes it), so Chinese sent that way arrives garbled; we build the PDU ourselves (SCA=00 = use the SIM's message center, semi-octet address, VP=A7 = 24h) to guarantee a correct on-air encoding, and verified byte-for-byte via `AT+CMGW`/`AT+CMGR` read-back. Numbers are sanitized to `0-9+*#` before every send (blocks AT-command injection via the recipient field). A command lock serializes sends to avoid clashing with polling, and the outbound queue is drained at most 3 messages per poll cycle so receiving is never starved during large batches.
- **Status collection**: `AT+CSQ` (signal), `AT+CREG?` (`0`/`1`/`5`), `AT+COPS?`, `AT+CGMM/CGMR/CGSN`, `AT+CPIN?`, `AT+CCID`, once per `STATUS_INTERVAL`.
- **Self-healing on disconnect**: a read exception triggers `on_fatal`; the worker auto-reconnects every `CONNECT_RETRY_INTERVAL` and re-runs the handshake (`AT` / `ATE0` / `AT+CMGF=1` / `AT+CSCS="UCS2"` / `AT+CNMI=2,1,0,0,0` / `AT+CLIP=1`).
- **Incoming-call notifications**: `+CLIP` event (de-duplicated for the same caller within 120 seconds) → pushes a "call" notification and is counted in `calls_total`.

## Air780E Reference Documentation

- [Luat Air780E module resource center](https://docs.openluat.com/air780e/)
- [Air780E AT command manual](https://docs.openluat.com/air780e/at/app/at_command/)
- [Air780E AT firmware versions](https://docs.openluat.com/air780e/at/firmware/)
- [LuaTools download and usage](https://docs.openluat.com/common/Luatools/)