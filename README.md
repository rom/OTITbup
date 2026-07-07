# OTITbup

OT and IT backup and restore tool that can extract and save configurations,
settings, logic and code etc from OT equipment (PLC, RTU, gateways, com
equipment), IT infrastructure equipment (network equipment), in a versioned
git backup.

- [REQUIREMENTS.md](REQUIREMENTS.md) — scope and decisions from the
  requirements interview
- [ARCHITECTURE.md](ARCHITECTURE.md) — module layout and design

## Quick start

```bash
pip install -e .[dev]          # core + tests
pip install -e .[ssh]          # + netmiko for network equipment

cp examples/otitbup.yml examples/secrets.yml .
$EDITOR otitbup.yml secrets.yml

otitbup validate               # check the config
otitbup list                   # show the inventory
otitbup backup                 # back up everything now
otitbup backup core-sw-01      # one device
otitbup diff core-sw-01        # latest change
otitbup log core-sw-01         # backup history
otitbup daemon                 # run the scheduler
otitbup serve                  # read-only web UI on 127.0.0.1:8080
otitbup passwd                 # hash a web UI password (webui.auth snippet)
otitbup discover 10.20.0.0/24  # sequential scan -> YAML proposal for review
otitbup restore plc-01 --out ./bundle   # hash-verified restore bundle
```

### Encrypted secrets

```bash
pip install -e .[crypto]
otitbup secrets genkey --out otitbup.key
otitbup secrets encrypt secrets.yml secrets.enc --key-file otitbup.key
otitbup secrets decrypt secrets.enc --key-file otitbup.key   # to view/edit
```

Then point the config at it (`backend: encryptedfile`, `path: secrets.enc`,
`key_file: otitbup.key` — or provide the key via `OTITBUP_KEY`).

## Status

**Drivers**: `generic_file` (watch-folder ingest of engineer-exported
projects), `generic_ssh` (multi-vendor network equipment via netmiko),
`siemens_s7` (python-snap7: MC7 block upload where the CPU allows it, CPU
info + program fingerprint always), `rockwell_enip` (pycomm3: controller
identity, tag list, program fingerprint), `schneider_modbus` (stdlib
Modbus device identification incl. loaded application name + fingerprint).

**Core**: local git store with per-device history and sha256 manifests;
maintenance windows and per-zone rate limiting; scheduler daemon; change
alerts (webhook/syslog/email); encrypted secrets tooling; web UI with
optional HTTP Basic auth (PBKDF2-hashed passwords via `otitbup passwd`);
opt-in sequential discovery that writes human-reviewed YAML proposals;
and a guided restore workflow — `otitbup restore` exports hash-verified
artifacts plus a RESTORE.md checklist, and never writes to devices.

Planned: UMAS/ACD program capture where vendors allow it, web UI TLS,
git-lfs for very large project files — see ARCHITECTURE.md.

## Tests

```bash
pytest
```
