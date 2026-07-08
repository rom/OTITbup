# Packaging & deployment

Two supported ways to run otitbup as a service. Both expect the config at
`/etc/otitbup/otitbup.yml` and keep state (git repo, blob store,
`runstore.db`) under `/var/lib/otitbup`.

## systemd (bare-metal appliance)

```bash
# 1. Install into a venv
sudo useradd --system --home-dir /var/lib/otitbup --create-home otitbup
sudo python3 -m venv /opt/otitbup/venv
sudo /opt/otitbup/venv/bin/pip install '/path/to/OTITbup[ssh,sftp,crypto]'

# 2. Config
sudo mkdir -p /etc/otitbup
sudo /opt/otitbup/venv/bin/otitbup -c /etc/otitbup/otitbup.yml init  # or copy your own

# 3. Units
sudo cp packaging/systemd/otitbup-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now otitbup-daemon otitbup-web
```

- Reload the inventory without downtime: `sudo systemctl reload otitbup-daemon`
  (sends SIGHUP; the daemon re-reads the config on the next tick).
- Liveness: the web unit answers `GET /healthz` (no auth) for monitoring.
- The units are hardened (`ProtectSystem=strict`, `NoNewPrivileges`,
  writable only under the state dir) — otitbup never writes to devices, so
  it needs no elevated privileges.

## Docker

```bash
docker build -f packaging/docker/Dockerfile -t otitbup .

# put otitbup.yml + secrets in ./packaging/docker/config/
docker compose -f packaging/docker/docker-compose.yml up -d
```

The image includes the `ssh,sftp,crypto` extras. For vendor PLC protocols
add the relevant extras to the `pip install` line in the Dockerfile
(`siemens`, `rockwell`, `opcua`, `beckhoff`). The container runs as a
non-root user and has a `HEALTHCHECK` on `/healthz`.

## SNMP MIB

`mibs/OTITBUP-MIB.txt` formally defines the trap OIDs emitted when
`events.snmp_trap` is configured. Load it into your NMS, and replace the
placeholder enterprise number `99999` with your organisation's IANA PEN in
both the MIB and the otitbup config.
