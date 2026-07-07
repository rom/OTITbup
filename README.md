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

Phase 1 (backup + diff): vendor-agnostic core with `generic_file`
(watch-folder ingest of engineer-exported projects), `generic_ssh`
(multi-vendor network equipment via netmiko), and `siemens_s7`
(python-snap7: block upload where the CPU allows it, CPU info + program
fingerprint always) drivers; local git store with per-device history and
sha256 manifests; maintenance windows and per-zone rate limiting; change
alerts (webhook/syslog/email); encrypted secrets tooling; and a read-only
web UI (`otitbup serve`) with device status, history, and diffs.

Rockwell and Schneider drivers, auto-discovery, web UI authentication, and
restore are planned — see ARCHITECTURE.md.

## Tests

```bash
pytest
```
