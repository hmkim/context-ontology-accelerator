# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for coa_serve.tier2.mapping_schemas."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from coa_serve.tier2.mapping_schemas import MappingSchemaResolver, table_schemas_from_r2rml

_R2RML = """
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix scl: <http://coa.amazon.com/vocab/coa#> .
@prefix ind: <https://example.org/ontology/> .

ind:ConditionOccurrenceMap a rr:TriplesMap ;
    scl:sourceSchema "cdm" ;
    rr:logicalTable [ rr:tableName "\\"condition_occurrence\\"" ] ;
    rr:subjectMap [ rr:template "https://example.org/co/{condition_occurrence_id}" ;
                    rr:class ind:ConditionOccurrence ] .

ind:PtMasterMap a rr:TriplesMap ;
    scl:sourceSchema "hosp" ;
    rr:logicalTable [ rr:tableName "\\"pt_master\\"" ] ;
    rr:subjectMap [ rr:template "https://example.org/pt/{patient_id}" ; rr:class ind:PtMaster ] .

# A disambiguated (schema-qualified) logical table: the real schema still comes
# from scl:sourceSchema, the qualifier inside the name is ignored.
ind:ClaimsMap a rr:TriplesMap ;
    scl:sourceSchema "sales" ;
    rr:logicalTable [ rr:tableName "\\"public__1a2b3c4d\\".\\"Claims\\"" ] ;
    rr:subjectMap [ rr:template "https://example.org/claims/{id}" ; rr:class ind:Claims ] .

# Same bare name in two schemas -> ambiguous, must be dropped.
ind:NotesA a rr:TriplesMap ;
    scl:sourceSchema "cdm" ;
    rr:logicalTable [ rr:tableName "\\"note\\"" ] ;
    rr:subjectMap [ rr:template "https://example.org/n/{id}" ; rr:class ind:NoteA ] .
ind:NotesB a rr:TriplesMap ;
    scl:sourceSchema "hosp" ;
    rr:logicalTable [ rr:tableName "\\"note\\"" ] ;
    rr:subjectMap [ rr:template "https://example.org/n2/{id}" ; rr:class ind:NoteB ] .

# No scl:sourceSchema -> contributes nothing.
ind:OrphanMap a rr:TriplesMap ;
    rr:logicalTable [ rr:tableName "\\"orphan\\"" ] ;
    rr:subjectMap [ rr:template "https://example.org/o/{id}" ; rr:class ind:Orphan ] .
"""


@pytest.mark.unit
class TestTableSchemasFromR2RML:
    def test_projects_bare_table_to_source_schema(self):
        schemas = table_schemas_from_r2rml(_R2RML)
        assert schemas["condition_occurrence"] == "cdm"
        assert schemas["pt_master"] == "hosp"

    def test_qualified_logical_table_uses_source_schema_not_the_qualifier(self):
        schemas = table_schemas_from_r2rml(_R2RML)
        assert schemas["claims"] == "sales"
        assert "public__1a2b3c4d" not in schemas.values()

    def test_ambiguous_bare_name_is_dropped(self):
        assert "note" not in table_schemas_from_r2rml(_R2RML)

    def test_triplesmap_without_source_schema_is_skipped(self):
        assert "orphan" not in table_schemas_from_r2rml(_R2RML)

    def test_accepts_bytes(self):
        assert table_schemas_from_r2rml(_R2RML.encode())["pt_master"] == "hosp"


def _s3_returning(body: bytes) -> MagicMock:
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}
    return s3


@pytest.mark.unit
class TestMappingSchemaResolver:
    async def test_disabled_without_bucket(self):
        resolver = MappingSchemaResolver(None)
        assert resolver.enabled is False
        assert await resolver.schemas_for("ns-1") == {}

    async def test_reads_latest_mapping_under_prefix_and_caches(self):
        s3 = _s3_returning(_R2RML.encode())
        resolver = MappingSchemaResolver("bucket", prefix="ontologies", s3_client=s3)
        first = await resolver.schemas_for("ns-1")
        second = await resolver.schemas_for("ns-1")
        assert first["pt_master"] == "hosp"
        assert second is first
        s3.get_object.assert_called_once_with(Bucket="bucket", Key="ontologies/ns-1/latest/mappings.r2rml")

    async def test_missing_object_degrades_to_empty_and_is_cached(self):
        s3 = MagicMock()
        s3.get_object.side_effect = RuntimeError("NoSuchKey")
        resolver = MappingSchemaResolver("bucket", s3_client=s3)
        assert await resolver.schemas_for("ns-1") == {}
        assert await resolver.schemas_for("ns-1") == {}
        assert s3.get_object.call_count == 1

    async def test_malformed_mapping_degrades_to_empty(self):
        resolver = MappingSchemaResolver("bucket", s3_client=_s3_returning(b"this is not turtle @@@"))
        assert await resolver.schemas_for("ns-1") == {}

    async def test_invalidate_forces_reread(self):
        s3 = _s3_returning(_R2RML.encode())
        resolver = MappingSchemaResolver("bucket", s3_client=s3)
        await resolver.schemas_for("ns-1")
        resolver.invalidate("ns-1")
        await resolver.schemas_for("ns-1")
        assert s3.get_object.call_count == 2

    async def test_empty_namespace_is_empty(self):
        resolver = MappingSchemaResolver("bucket", s3_client=_s3_returning(_R2RML.encode()))
        assert await resolver.schemas_for("") == {}
