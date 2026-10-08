# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Structural-fingerprint canonicalization (duplicate-induction detection).

The fingerprint must be stable across scans of the SAME logical schema even
when the connector reports types/constraints differently — otherwise a re-scan
of an already-inducted source fails to dedupe and runs a fresh induction.
Regression for the observed case: a JDBC scan reporting ``character varying`` /
``constraint=None`` produced a different fingerprint than an earlier scan of
the same insurance schema.
"""

from __future__ import annotations

import pytest
from coa_ontology.induce_catalog import (
    _compute_curation_fingerprint,
    _compute_structural_fingerprint,
    _is_duplicate_of,
)
from coa_ontology.inducer.services.data_catalog import (
    CatalogColumn,
    CatalogConstraint,
    CatalogTable,
)

pytestmark = pytest.mark.unit


def _tbl(name, cols, cons=None):
    return CatalogTable(id=f"t-{name}", name=name, fullyQualifiedName=f"db.{name}", columns=cols, tableConstraints=cons)


class TestFingerprintCanonicalization:
    def test_equivalent_type_spellings_hash_identically(self):
        """``character varying`` (PG) and ``VARCHAR`` (Glue) → same fingerprint."""
        pg = _tbl(
            "policy",
            [CatalogColumn(name="id", dataType="integer"), CatalogColumn(name="num", dataType="character varying")],
        )
        glue = _tbl("policy", [CatalogColumn(name="id", dataType="INT"), CatalogColumn(name="num", dataType="VARCHAR")])
        assert _compute_structural_fingerprint([pg]) == _compute_structural_fingerprint([glue])

    def test_constraint_detection_difference_does_not_change_fingerprint(self):
        """A scan that detects PK/NOT NULL vs one that reports constraint=None
        must still dedupe — constraint detection isn't part of class identity."""
        with_cons = _tbl(
            "policy",
            [CatalogColumn(name="id", dataType="INTEGER", constraint="PRIMARY_KEY")],
            [CatalogConstraint(constraintType="PRIMARY_KEY", columns=["id"])],
        )
        without_cons = _tbl("policy", [CatalogColumn(name="id", dataType="INTEGER", constraint=None)])
        assert _compute_structural_fingerprint([with_cons]) == _compute_structural_fingerprint([without_cons])

    def test_fk_fqn_vs_bare_reference_hash_identically(self):
        """FK referredColumns ``db.customers.id`` and ``customers.id`` match."""
        fqn = _tbl(
            "orders",
            [CatalogColumn(name="customer_id", dataType="INT")],
            [
                CatalogConstraint(
                    constraintType="FOREIGN_KEY", columns=["customer_id"], referredColumns=["db.customers.id"]
                )
            ],
        )
        bare = _tbl(
            "orders",
            [CatalogColumn(name="customer_id", dataType="INT")],
            [
                CatalogConstraint(
                    constraintType="FOREIGN_KEY", columns=["customer_id"], referredColumns=["customers.id"]
                )
            ],
        )
        assert _compute_structural_fingerprint([fqn]) == _compute_structural_fingerprint([bare])

    def test_genuinely_different_schema_differs(self):
        """A real structural change (extra column) MUST produce a new fingerprint."""
        base = _tbl("policy", [CatalogColumn(name="id", dataType="INT")])
        extended = _tbl(
            "policy", [CatalogColumn(name="id", dataType="INT"), CatalogColumn(name="premium", dataType="DECIMAL")]
        )
        assert _compute_structural_fingerprint([base]) != _compute_structural_fingerprint([extended])

    def test_different_type_class_differs(self):
        """Same column name, genuinely different type class → different fingerprint."""
        as_int = _tbl("t", [CatalogColumn(name="c", dataType="INTEGER")])
        as_str = _tbl("t", [CatalogColumn(name="c", dataType="VARCHAR")])
        assert _compute_structural_fingerprint([as_int]) != _compute_structural_fingerprint([as_str])

    def test_table_order_independent(self):
        """Fingerprint is order-independent across the table list."""
        a = _tbl("a", [CatalogColumn(name="id", dataType="INT")])
        b = _tbl("b", [CatalogColumn(name="id", dataType="INT")])
        assert _compute_structural_fingerprint([a, b]) == _compute_structural_fingerprint([b, a])


class TestCurationFingerprint:
    """Steward text (descriptions, synonyms) is hashed separately from structure."""

    def _pair(self):
        before = _tbl(
            "condition_occurrence",
            [
                CatalogColumn(name="person_id", dataType="int"),
                CatalogColumn(name="condition_concept_id", dataType="int"),
            ],
        )
        after = _tbl(
            "condition_occurrence",
            [
                CatalogColumn(name="person_id", dataType="int"),
                CatalogColumn(
                    name="condition_concept_id",
                    dataType="int",
                    description="표준 진단 개념 ID. 제2형 당뇨병 = 201826 및 하위 개념",
                    synonyms=["진단 코드"],
                ),
            ],
        )
        after.description = "진단(상병) 기록. 당뇨 활성 환자 = 최근 12개월 T2DM 진단"
        after.synonyms = ["진단", "상병"]
        return before, after

    def test_structure_unchanged_by_curation(self):
        before, after = self._pair()
        assert _compute_structural_fingerprint([before]) == _compute_structural_fingerprint([after])

    def test_curation_changes_the_curation_fingerprint(self):
        before, after = self._pair()
        assert _compute_curation_fingerprint([before]) != _compute_curation_fingerprint([after])

    def test_whitespace_and_synonym_order_do_not_matter(self):
        a = _tbl("t", [CatalogColumn(name="c", dataType="int", description=" x ", synonyms=["b", "a"])])
        b = _tbl("t", [CatalogColumn(name="c", dataType="int", description="x", synonyms=["a", "b"])])
        assert _compute_curation_fingerprint([a]) == _compute_curation_fingerprint([b])

    def test_table_order_independent(self):
        a = _tbl("a", [CatalogColumn(name="c", dataType="int")])
        b = _tbl("b", [CatalogColumn(name="c", dataType="int")])
        assert _compute_curation_fingerprint([a, b]) == _compute_curation_fingerprint([b, a])


class TestDuplicateDecision:
    def test_same_structure_and_curation_is_duplicate(self):
        assert _is_duplicate_of({"structural_fingerprint": "s1", "curation_fingerprint": "c1"}, "s1", "c1") is True

    def test_same_structure_different_curation_is_not_duplicate(self):
        """The steward edited descriptions and re-induced: a new proposal must be built."""
        meta = {"structural_fingerprint": "s1", "curation_fingerprint": "c1"}
        assert _is_duplicate_of(meta, "s1", "c2") is False

    def test_different_structure_is_not_duplicate(self):
        assert _is_duplicate_of({"structural_fingerprint": "s1", "curation_fingerprint": "c1"}, "s2", "c1") is False

    def test_legacy_proposal_without_curation_fingerprint_is_not_duplicate(self):
        """Nothing proves a pre-upgrade accepted proposal's text matches the catalog now."""
        assert _is_duplicate_of({"structural_fingerprint": "s1"}, "s1", "c1") is False
