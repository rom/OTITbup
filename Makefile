# otitbup — install, develop, test.
#
#   make install            core install (stdlib-only feature set)
#   make install-devices    + every supported device/driver dependency
#   make install-extensions + every extension/integration dependency
#   make install-all        core + devices + extensions in one go
#   make dev                editable install with the dev toolchain
#   make test / lint        run pytest / ruff
#
# Device extras map to pyproject [project.optional-dependencies]:
#   ssh      network gear over SSH CLI (netmiko)
#   sftp     SFTP/SCP file fetch drivers (paramiko)
#   siemens  Siemens S7 PLCs (python-snap7)
#   rockwell Rockwell/Allen-Bradley Logix (pycomm3)
#   opcua    OPC UA servers (asyncua)
#   beckhoff Beckhoff TwinCAT ADS (pyads)
# Extension/integration extras:
#   crypto   encrypted secrets/blob store, TLS certgen (cryptography)
#   ldap     LDAP / Active Directory login (ldap3)

PIP        ?= python3 -m pip
DEVICE_EXTRAS     = ssh,sftp,siemens,rockwell,opcua,beckhoff
EXTENSION_EXTRAS  = crypto,ldap

.PHONY: install install-devices install-extensions install-all dev test lint clean

install:
	$(PIP) install .

install-devices:
	$(PIP) install ".[$(DEVICE_EXTRAS)]"

install-extensions:
	$(PIP) install ".[$(EXTENSION_EXTRAS)]"

install-all:
	$(PIP) install ".[$(DEVICE_EXTRAS),$(EXTENSION_EXTRAS)]"

dev:
	$(PIP) install -e ".[$(DEVICE_EXTRAS),$(EXTENSION_EXTRAS),dev]"

test:
	python3 -m pytest

lint:
	python3 -m ruff check src tests

clean:
	rm -rf build dist src/*.egg-info .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
