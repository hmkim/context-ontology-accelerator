# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""coa_common.tls — CA bundle used as ``cafile`` for TLS-verified DB drivers."""

from __future__ import annotations

from pathlib import Path

import pytest
from coa_common import tls


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(tls, "_cached", None)
    monkeypatch.delenv(tls.CA_BUNDLE_ENV, raising=False)


@pytest.mark.unit
def test_vendored_rds_bundle_is_present_and_is_pem():
    assert tls.RDS_GLOBAL_BUNDLE.is_file()
    text = tls.RDS_GLOBAL_BUNDLE.read_text()
    # The global bundle carries every regional RDS CA (100+ certificates).
    assert text.count("-----BEGIN CERTIFICATE-----") > 50


@pytest.mark.unit
def test_env_override_wins_verbatim(monkeypatch, tmp_path):
    custom = tmp_path / "corp-ca.pem"
    custom.write_text("-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n")
    monkeypatch.setenv(tls.CA_BUNDLE_ENV, str(custom))
    assert tls.db_ca_bundle_path() == str(custom)


@pytest.mark.unit
def test_merged_bundle_contains_certifi_and_rds(monkeypatch, tmp_path):
    monkeypatch.setattr(tls.tempfile, "gettempdir", lambda: str(tmp_path))
    path = Path(tls.db_ca_bundle_path())
    assert path.parent == tmp_path
    merged = path.read_bytes()
    import certifi

    assert Path(certifi.where()).read_bytes() in merged
    assert tls.RDS_GLOBAL_BUNDLE.read_bytes() in merged
    # Second call is served from the cache — same path, no rewrite.
    assert tls.db_ca_bundle_path() == str(path)


@pytest.mark.unit
def test_falls_back_to_rds_bundle_without_certifi(monkeypatch):
    monkeypatch.setattr(tls, "_certifi_bundle", lambda: None)
    assert tls.db_ca_bundle_path() == str(tls.RDS_GLOBAL_BUNDLE)
