# OTITbup hardening checklist

A short, opinionated checklist for a secure deployment. Each item points at
the `otitbup.yml` key(s) that implement it — see
[`examples/high-security.yml`](high-security.yml) for a config that sets all
of them, and [`docs/CONFIGURATION.md`](../docs/CONFIGURATION.md) for the full
reference.

## Secrets

- [ ] Never keep device credentials in plaintext on the appliance.
      Use `secrets.backend: encryptedfile` (`path`, `key_file` / `OTITBUP_KEY`),
      or `vault` / `cyberark` for centralised secrets.
- [ ] Keep the secrets key OUT of the repo and off any offsite copy.
      Provide it via `OTITBUP_KEY` in the systemd unit's environment, not on disk
      where possible.
- [ ] Credentials are never written into the git backup — verify with
      `otitbup search` for any known secret string (should return nothing).

## Encryption at rest

- [ ] Encrypt the offloaded blob store: `encryption.blob_key_file`
      (Fernet; content-addressing is by plaintext hash, so dedup is unaffected).
- [ ] Full-disk encryption (LUKS) on the appliance — `encryption.blob_key`
      covers ONLY the blob store; the git repo, `runstore.db` and secrets rely on
      LUKS.

## Backup integrity / tamper evidence

- [ ] Sign every backup commit: `git.sign.key_file` (SSH key). Verify with
      `git log --show-signature`.
- [ ] The audit log is hash-chained automatically; run `otitbup verify-audit`
      on a schedule (exit 0 = intact, nonzero = the first tampered row).
- [ ] Verify stored artifacts against their capture-time manifests:
      `otitbup verify` (re-hashes) on a schedule.

## Access control (web UI + API)

- [ ] Bind the UI to loopback (`webui.host: 127.0.0.1`) and reach it only
      through a TLS-terminating reverse proxy.
- [ ] Enable TLS even on loopback: `webui.tls.cert_file` / `key_file`
      (`otitbup certgen` or CA-issued PEMs).
- [ ] Define least-privilege users with roles and scopes: `webui.users`
      (`role: viewer|operator|admin`, `scopes: "plant-a/*"`). Hash passwords with
      `otitbup passwd`.
- [ ] Delegate login to SSO only behind the proxy: `webui.trusted_header`
      (+ `trusted_role_header`, `trusted_default_role`). The header is spoofable if
      the UI is directly reachable — that is why `host` stays on loopback.
- [ ] Optional directory login: `ldap` (local users tried first, then LDAP;
      `role_map` maps groups to roles).
- [ ] Mint scoped, expiring API tokens for CI/automation:
      `otitbup token create <name> --role operator --scopes "plant-a/*" --days 90`
      (stored sha256-hashed, shown once).

## OT safety (do no harm to the process)

- [ ] Every driver is read-only by contract — nothing here writes to a
      PLC/RTU. Keep `net-restore` (network gear only) as dry-run; add `--apply`
      only deliberately.
- [ ] Constrain polling with `zone.maintenance_window` (+ `zone.timezone`)
      and `zone.max_concurrent: 1` (strictly sequential) for control networks.
- [ ] Set device `schedule` (cron or interval) so backups land inside the
      maintenance window.

## Observability / incident response

- [ ] Stream operational events to the SIEM: `events.syslog` and/or
      `events.snmp_trap` (MIB in `mibs/OTITBUP-MIB.txt`; set your own
      `enterprise_oid`).
- [ ] Alert on silent failure: `alerts.stale_days` (no successful backup in
      N days), plus `alerts.syslog` / `email` / `webhooks`.
- [ ] Open tickets on `backup.error` / `change.unexpected` via `tickets`.
- [ ] Enable statistical `anomaly` detection (slow backup, change storm,
      flapping, slow trend).

## Resilience (3-2-1-1-0)

- [ ] Mirror off-appliance: `git.push` + `git.remote`, marked
      `strategy.offsite: true`.
- [ ] Keep an encrypted offsite/cloud snapshot: `offsite` (`file`/`sftp`/`s3`)
      with a `key_file` kept off the remote.
- [ ] Keep an offline/air-gapped copy: `otitbup export` to removable media,
      referenced by `strategy.offline.path` (+ `max_age_days`).
- [ ] Confirm posture with `otitbup strategy` (3 copies, 2 media, 1 offsite,
      1 offline, 0 errors).
