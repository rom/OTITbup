# OTITbup — Configuration reference

OTITbup is configured by two files, both plain text and both meant to be
managed like code:

| File | Purpose | Version in git? |
|---|---|---|
| `otitbup.yml` | Inventory + application settings (this document) | **Yes** — it is the source of truth |
| `secrets.yml` / `secrets.enc` | Device credentials | No — plaintext protected by OS permissions, or Fernet-encrypted |

Every command takes `-c/--config` (default: `./otitbup.yml`). Validate
after any edit with `otitbup validate`. A complete annotated example
lives in [`examples/otitbup.yml`](../examples/otitbup.yml).

## otitbup.yml — top level

```yaml
data_dir: ./data        # backup git repository; relative paths resolve
                        # against the config file's directory
secrets: { ... }        # credential backend        (section below)
git:     { ... }        # remote mirroring          (section below)
webui:   { ... }        # web UI: host/port/auth/TLS (section below)
alerts:  { ... }        # change/failure alerts     (section below)
sites:   [ ... ]        # the inventory             (section below)
```

## The inventory: sites → zones → devices

```yaml
sites:
  - name: plant-a                    # required, unique
    zones:
      - name: cell-1                 # required, unique within the site
        maintenance_window: "18:00-06:00"   # optional, HH:MM-HH:MM;
                                            # overnight ranges allowed;
                                            # devices are only polled
                                            # inside the window
        max_concurrent: 1            # simultaneous connections into this
                                     # zone (default 1 = strictly
                                     # sequential; OT-safe)
        devices:
          - name: plc-01             # required; unique as site/zone/name
            driver: siemens_s7       # required; see `otitbup drivers`
            address: 10.20.0.10      # IP/hostname (driver-dependent)
            schedule: 12h            # poll interval: Ns / Nm / Nh / Nd
                                     # (default 1d); used by `daemon`
            credentials: plc-01      # optional: key into the secrets file
            options: { ... }         # driver-specific, see below
```

Device names can be used bare on the CLI (`otitbup backup plc-01`) when
unambiguous, or fully qualified (`plant-a/cell-1/plc-01`).

### Driver options

Options are driver-specific; each driver's Python docstring is the
authoritative reference, and `otitbup drivers` lists everything with a
one-line description. The recurring ones:

| Option | Used by | Meaning |
|---|---|---|
| `port` | most network drivers | TCP port override |
| `commands` | SSH drivers/profiles | CLI commands to capture (one artifact each) |
| `device_type` | SSH drivers/profiles | netmiko device type override |
| `scrub` | SSH drivers/profiles | regexes for volatile lines to drop before diffing |
| `paths` | `generic_sftp` family | remote files/dirs/globs to fetch |
| `urls` | `generic_http` family | export endpoints; `{address}` is substituted |
| `verify_tls` | `generic_http` family | set `false` for self-signed device certs |
| `rack`, `slot` | `siemens_s7` | CPU position (300/400: slot 2; 1200/1500: usually 1) |
| `slot` | `rockwell_enip` | controller backplane slot |
| `unit_id` | `schneider_modbus` | Modbus unit (255 = direct addressing) |
| `outstation`, `master` | `generic_dnp3` | DNP3 link addresses |
| `ams_net_id`, `ams_port` | `beckhoff_ads` | ADS target (default `<address>.1.1`, 851) |
| `endpoint`, `nodes` | `generic_opcua` | endpoint URL override; extra nodes to read |
| `max_file_size` | file-fetch drivers | skip files larger than this (default 100 MiB) |

Vendor SSH profiles (e.g. `cisco_ios`, `hirschmann_hios`) are presets —
any of `device_type`, `commands`, `port`, `scrub` set in `options`
overrides the preset for that device.

## secrets

```yaml
secrets:
  backend: plainfile     # plainfile | encryptedfile
  path: secrets.yml      # relative to the config file's directory
  # key_file: otitbup.key   # encryptedfile only; or set OTITBUP_KEY
```

The secrets file maps credential names (referenced by `credentials:` on
devices) to per-device values:

```yaml
plc-01:
  username: backup
  password: s3cret
  # enable_secret: ...   # cisco_ios/cisco_asa privileged mode
```

Encrypted workflow:

```bash
otitbup secrets genkey --out otitbup.key            # key file, mode 0600
otitbup secrets encrypt secrets.yml secrets.enc --key-file otitbup.key
otitbup secrets decrypt secrets.enc --key-file otitbup.key   # view/edit
```

Then set `backend: encryptedfile`, `path: secrets.enc`, and either
`key_file` or the `OTITBUP_KEY` environment variable on the service.

## git (remote mirroring)

```yaml
git:
  push: true
  remote: git@gitlab.plant.local:ot/backups.git
```

The local repository in `data_dir` is always the primary store; when
`push` is enabled the repo is mirrored to `remote` after each backup run.
Push failures are logged, never fatal.

## webui

```yaml
webui:
  host: 127.0.0.1        # bind address (default loopback)
  port: 8080
  auth:                  # HTTP Basic; generate with `otitbup passwd`
    username: admin
    password_hash: pbkdf2_sha256$600000$<salt>$<hash>
  tls:                   # HTTPS; generate a self-signed pair with
    cert_file: webui-cert.pem       # `otitbup certgen`, or use
    key_file: webui-key.pem         # CA-issued PEM files
```

`cert_file`/`key_file` resolve relative to the config file's directory.
Binding beyond loopback without `auth` logs a loud warning. The UI is
strictly read-only.

## alerts

```yaml
alerts:
  webhooks:
    - https://hooks.example.com/otitbup   # JSON POST per alert
  syslog:
    address: 10.0.0.1
    port: 514
  email:
    smtp_host: mail.plant.local
    smtp_port: 25
    from: otitbup@plant.local
    to: [ot-team@example.com]
```

Alerts fire when a backup run detects changes (one alert summarising the
changed devices) and when backups fail. Delivery failures are logged and
never block backups.

## Files next to the config

| File | Created by | Notes |
|---|---|---|
| `data/` | `otitbup backup` | the backup git repository (`data_dir`) |
| `state.json` | `otitbup daemon` | per-device last-run times; safe to delete (forces a run) |
| `otitbup.key` | `otitbup secrets genkey` | Fernet key, mode 0600 |
| `webui-cert.pem`, `webui-key.pem` | `otitbup certgen` | TLS pair, key mode 0600 |
| `discovered.yml` | `otitbup discover` | inventory proposal for human review |
