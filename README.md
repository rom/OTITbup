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
```

## Status

Phase 1 (backup + diff) MVP: vendor-agnostic core with `generic_file`
(watch-folder ingest of engineer-exported projects) and `generic_ssh`
(multi-vendor network equipment via netmiko) drivers, local git store with
per-device history and sha256 manifests, maintenance windows, per-zone rate
limiting, and change alerts (webhook/syslog/email).

Vendor protocol drivers (Siemens S7, Rockwell, Schneider), the web UI, and
restore are planned — see ARCHITECTURE.md.

## Tests

```bash
pytest
```
