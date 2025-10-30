[English](./README.md) | [日本語](./README.ja.md)

# certbot-to-asa

A Certbot *deploy hook* script that **automatically deploys Let’s Encrypt TLS certificates to Cisco ASA** firewalls.
Designed for environments that expose public services such as **Web VPN / AnyConnect portals** on the `outside` interface.

> **Note:** The maximum validity of public TLS certificates is expected to decrease further (currently **47 days**).
> Please check the latest information from CA/B Forum Ballots before deployment.
>
> <img width="1229" height="625" alt="Screenshot 2025-10-28 at 15 14 32" src="https://github.com/user-attachments/assets/c511ff2f-6b89-439a-838f-92720b5ec743" />

---

## Features

* Automatically triggered by Certbot’s **deploy hook** immediately after renewal
* Connects to ASA via SSH and **imports the new certificate as PKCS#12 (Base64)**
* Detects differences in **certificate serial numbers** and updates only when necessary
* Reconfigures `ssl trust-point ... outside` and runs **write memory** automatically
* Safely skips update if the serial number is identical
* Logs all operations to `/var/log/letsencrypt/asa.log`

---

## Requirements

* Cisco ASA (version 9.12 or later recommended; must support PKCS#12 import)
* Certbot (with `renewal-hooks/deploy` directory available)
* Linux (Debian / Ubuntu or equivalent)
* `python3`, `pexpect`, `openssl`

---

## How It Works

1. Certbot renews the TLS certificate.
2. The deploy hook script (`certbot-to-asa.py`) is triggered.
3. It compares **serial numbers** of the new and current certificates.
4. Converts the new certificate and private key to **PKCS#12 + Base64**.
5. Logs in to ASA via SSH and retrieves the serial number of the current trustpoint.
6. If they differ:

   * Creates a new trustpoint (`LE-Portal-YYYYMMDDHHMMSS`)
   * Sends Base64-encoded PKCS#12 via `crypto ca import ... pkcs12`
   * Rebinds `ssl trust-point ... outside` and executes `write memory`
   * Deletes the old trustpoint and its certificate chain
7. If identical, safely skips the update and exits.

---

## Recommended Directory Layout

```text
/etc/letsencrypt/
  renewal-hooks/
    deploy/
      certbot-to-asa.py                 # ← The main script (root:root 0750)
  credentials/
    asa.pass                            # ← LOGIN/ENABLE passwords (root:root 0600)
  hooks.d/
    asa.env                             # ← Non-sensitive environment config (root:root 0640)
/var/log/letsencrypt/
  asa.log                               # ← Log file (root:adm 0640)
```

> **Note:**
> You can override the default env file location with the environment variable `ASA_ENV_FILE`.
> Example:
> `ASA_ENV_FILE=/etc/letsencrypt/hooks.d/asa.env`

---

## Configuration

### 1) ASA Password File (`asa.pass`)

```bash
# /etc/letsencrypt/credentials/asa.pass (600)
LOGIN=YourLoginPassword
ENABLE=YourEnablePassword
```

---

### 2) Environment File (`asa.env`)

```bash
# /etc/letsencrypt/hooks.d/asa.env (640)

ASA_HOST=asa.example.com
ASA_USER=admin
ASA_PASSWORD_FILE=/etc/letsencrypt/credentials/asa.pass

# Certificate parameters
ASA_DOMAIN=example.com
ASA_PKCS12_PASS=exportpass
```

> **Tip:**
> The script automatically uses the environment variable `RENEWED_LINEAGE` provided by Certbot.
> For manual testing, you need to set it explicitly.

---

## Usage

### A. Automatic (Certbot Deploy Hook)

The script runs automatically when `certbot renew` is executed.
No additional configuration is required.

---

### B. Manual Test (Verification)

```bash
sudo -E RENEWED_LINEAGE=/etc/letsencrypt/live/example.com \
  python3 -u /etc/letsencrypt/renewal-hooks/deploy/certbot-to-asa.py
```

> **Note:**
> If the serial number matches, `[INFO] Certificate is up-to-date.` will appear, and no ASA modification occurs.

---

## Default Behavior (Safe Design)

* Skips update when the certificate serial number matches.
* If different, creates a new trustpoint and rebinds `ssl trust-point`.
* Removes the old trustpoint automatically.
* Generates PKCS#12 as a temporary file, which is deleted after import.

---

## Troubleshooting

### 1) Env File Not Loaded

```bash
sudo -E RENEWED_LINEAGE=/etc/letsencrypt/live/example.com \
  python3 -u /etc/letsencrypt/renewal-hooks/deploy/certbot-to-asa.py
```

If `[WARN] Env file not found:` appears, specify the env file explicitly using `ASA_ENV_FILE`.

---

### 2) SSH Login Failed

```
[ERR] SSH login failed: Permission denied.
```

* Check that the `LOGIN` password in `asa.pass` is correct.
* Ensure the `ASA_USER` account has privilege level 15.
* The script assumes **password-based login**.
  (Modify `pexpect` handling if using SSH key authentication.)

---

### 3) Certificate Not Updated

* The serial numbers match — this is **expected** (no update required).
* Verify your local serial with `openssl x509 -noout -serial`.

---

### 4) Timeout During Import

* Check ASA console output or logs (`show logging`).
* For large certificate chains (>4 KB), increase timeout in the script (`pexpect.timeout`).

---

## Security

* `/etc/letsencrypt/credentials/asa.pass` must be **root:root 0600**.
* Do not store passwords directly in `.env` files.
* Use a strong password for `ASA_PKCS12_PASS`.
* Sensitive data is not logged to `/var/log/letsencrypt/asa.log`.

---

## Known Issues

* Older ASA versions may not support `crypto ca import ... pkcs12`.
* Terminal color codes in ASA CLI output may break `pexpect` pattern matching.
* Running another `crypto ca` command concurrently from a second session can cause conflicts.

---

## License

License: **0BSD**
The author waives all rights and claims related to this repository, including moral rights.

---

## Author

**Hideaki Shimomura**


