# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""CA bundle for TLS-encrypted source-database connections.

python-tds (SQL Server) only encrypts the data stream when ``cafile`` is
supplied, and then verifies the server certificate against that file. RDS for
SQL Server signs its certificates with the Amazon RDS CAs, which are not in
the public trust store, so a plain ``certifi`` bundle is not enough: when the
server enforces ``rds.force_ssl=1`` the login fails with

    Client does not have encryption enabled but it is required by server

This module builds ONE bundle that trusts both worlds:

* the public CAs shipped with ``certifi`` (SQL Server behind a public CA), and
* the Amazon RDS global CA bundle vendored at ``certs/rds-global-bundle.pem``
  (https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem).

Operators who front SQL Server with a private CA point ``COA_DB_CA_BUNDLE`` at
their own PEM file; that path is used verbatim and nothing is merged.
"""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

CA_BUNDLE_ENV = "COA_DB_CA_BUNDLE"
RDS_GLOBAL_BUNDLE = Path(__file__).parent / "certs" / "rds-global-bundle.pem"

_lock = threading.Lock()
_cached: str | None = None


def _certifi_bundle() -> Path | None:
    try:
        import certifi  # noqa: PLC0415
    except ImportError:
        return None
    path = Path(certifi.where())
    return path if path.is_file() else None


def db_ca_bundle_path() -> str:
    """Return the path of a PEM bundle to pass as the driver's ``cafile``.

    Resolution order:

    1. ``COA_DB_CA_BUNDLE`` when set (operator-supplied private CA).
    2. A merged file (certifi public CAs + Amazon RDS CAs) written once per
       process into the temp directory — the only writable location on Lambda
       and in a read-only container filesystem.
    3. The vendored RDS bundle alone when certifi is not installed.

    The merged file is cached for the process lifetime, so the cost is paid on
    the first connection only.
    """
    override = os.environ.get(CA_BUNDLE_ENV)
    if override:
        return override

    global _cached
    if _cached and Path(_cached).is_file():
        return _cached

    with _lock:
        if _cached and Path(_cached).is_file():
            return _cached
        certifi_bundle = _certifi_bundle()
        if certifi_bundle is None:
            _cached = str(RDS_GLOBAL_BUNDLE)
            return _cached

        merged = Path(tempfile.gettempdir()) / f"coa-db-ca-bundle-{os.getpid()}.pem"
        with merged.open("wb") as out:
            out.write(certifi_bundle.read_bytes())
            out.write(b"\n")
            out.write(RDS_GLOBAL_BUNDLE.read_bytes())
        _cached = str(merged)
        return _cached
