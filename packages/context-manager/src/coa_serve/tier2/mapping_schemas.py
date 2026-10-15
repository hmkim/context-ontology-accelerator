# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-table source schemas for a namespace, read from its published R2RML mapping.

The VKG path knows which schema every table lives in: the translate server loads
``ontologies/<namespace>/latest/mappings.r2rml`` and returns a
``datasourceRouting`` entry per table with ``coa:sourceSchema``. The NL->SQL path
does not — its retrieval hits carry ``data_source_id`` but no schema — so it
falls back to the per-SOURCE schema (:func:`..table_qualifier.schema_for_source`),
which is documented as wrong for a source spanning several schemas. On an engine
with no session default-schema switch (SQL Server) that leaves every bare
reference unresolvable.

This module reads the SAME mapping the VKG consumes and projects it to
``{bare_table_name: source_schema}``, so both Tier-2 strategies qualify a table
the same way. The read is cached per namespace for a short TTL (the mapping only
changes on an ontology accept) and every failure degrades to an empty map, which
leaves the caller on today's behaviour.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import structlog
from coa_common.constants import VOCAB_URI, split_sql_ident_path
from rdflib import Graph, Namespace

logger = structlog.get_logger(__name__)

_RR = Namespace("http://www.w3.org/ns/r2rml#")
_SCL = Namespace(VOCAB_URI)

# How long one namespace's table->schema map is reused before the mapping is
# re-read. A mapping changes only when a proposal is accepted (which also
# republishes the VKG), so staleness here is bounded and harmless: a table that
# gains a schema late is simply qualified on the next read.
_DEFAULT_TTL_S = float(os.environ.get("MAPPING_SCHEMAS_TTL_S", "300"))
_DEFAULT_PREFIX = "ontologies/"
_MAPPING_KEY = "latest/mappings.r2rml"


def table_schemas_from_r2rml(r2rml: str | bytes) -> dict[str, str]:
    """Project an R2RML mapping to ``{bare_table_lower: source_schema}``.

    Each TriplesMap's ``rr:logicalTable/rr:tableName`` names the table (bare, or
    ``"schema"."table"`` when the inducer had to disambiguate a shared name), and
    the inducer's ``coa:sourceSchema`` annotation on the TriplesMap names the REAL
    schema. Only the real schema is used; a quoted/synthetic qualifier inside the
    table name is discarded. A bare name claimed by two different schemas is
    dropped from the map (ambiguous — the qualifier refuses those statements
    upstream), so the result never asserts a schema it cannot be sure of.
    """
    return _project_r2rml(r2rml, _SCL.sourceSchema, "mapping_schemas_ambiguous_dropped")


def table_sources_from_r2rml(r2rml: str | bytes) -> dict[str, str]:
    """Project an R2RML mapping to ``{bare_table_lower: datasource_id}``.

    The inducer annotates every TriplesMap with ``coa:datasourceId`` — the source
    the table was scanned from — so the published mapping is an authoritative
    table->source attribution for EVERY mapped table of the namespace, not only the
    ones a particular retrieval happened to return. Same ambiguity rule as
    :func:`table_schemas_from_r2rml`: a bare name two sources both expose is dropped.
    """
    return _project_r2rml(r2rml, _SCL.datasourceId, "mapping_sources_ambiguous_dropped")


def _project_r2rml(r2rml: str | bytes, predicate: Any, dropped_event: str) -> dict[str, str]:
    graph = Graph()
    graph.parse(data=r2rml if isinstance(r2rml, str) else r2rml.decode(), format="turtle")
    claims: dict[str, set[str]] = {}
    for tmap in set(graph.subjects(_RR.logicalTable, None)):
        value = graph.value(tmap, predicate)
        if value is None:
            continue
        for logical in graph.objects(tmap, _RR.logicalTable):
            name = graph.value(logical, _RR.tableName)
            if name is None:
                continue
            parts = split_sql_ident_path(str(name))
            if not parts:
                continue
            bare = parts[-1].strip().lower()
            if bare:
                claims.setdefault(bare, set()).add(str(value))
    result = {bare: next(iter(values)) for bare, values in claims.items() if len(values) == 1}
    dropped = sorted(bare for bare, values in claims.items() if len(values) > 1)
    if dropped:
        logger.info(dropped_event, tables=dropped)
    return result


class MappingSchemaResolver:
    """Cached ``namespace -> {table: schema}`` / ``{table: datasource}`` lookups over the published mapping."""

    def __init__(
        self,
        bucket: str | None,
        *,
        prefix: str = _DEFAULT_PREFIX,
        ttl_s: float = _DEFAULT_TTL_S,
        s3_client: Any | None = None,
    ) -> None:
        """Bind the artifacts bucket/prefix; ``bucket`` None or empty disables the resolver."""
        self._bucket = (bucket or "").strip()
        self._prefix = prefix if prefix.endswith("/") else prefix + "/"
        self._ttl_s = ttl_s
        self._s3 = s3_client
        # One cache entry per namespace holds BOTH projections of the same read:
        # (expiry, {table: schema}, {table: datasource_id}).
        self._cache: dict[str, tuple[float, dict[str, str], dict[str, str]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        if self._bucket:
            logger.info("mapping_schemas_enabled", bucket=self._bucket, prefix=self._prefix)
        else:
            logger.info("mapping_schemas_disabled", reason="no bucket configured")

    @property
    def enabled(self) -> bool:
        """True when a bucket is configured (otherwise every lookup returns ``{}``)."""
        return bool(self._bucket)

    def _client(self) -> Any:
        if self._s3 is None:
            import boto3  # noqa: PLC0415 — deferred so unit tests never need a region

            self._s3 = boto3.client("s3")
        return self._s3

    def _key(self, namespace: str) -> str:
        return f"{self._prefix}{namespace}/{_MAPPING_KEY}"

    async def schemas_for(self, namespace: str) -> dict[str, str]:
        """Return the namespace's ``{bare_table: schema}`` map (``{}`` on any failure)."""
        return (await self._maps_for(namespace))[0]

    async def sources_for(self, namespace: str) -> dict[str, str]:
        """Return the namespace's ``{bare_table: datasource_id}`` map (``{}`` on any failure)."""
        return (await self._maps_for(namespace))[1]

    async def _maps_for(self, namespace: str) -> tuple[dict[str, str], dict[str, str]]:
        if not self._bucket or not namespace:
            return {}, {}
        now = time.monotonic()
        cached = self._cache.get(namespace)
        if cached and cached[0] > now:
            return cached[1], cached[2]
        lock = self._locks.setdefault(namespace, asyncio.Lock())
        async with lock:
            cached = self._cache.get(namespace)
            if cached and cached[0] > time.monotonic():
                return cached[1], cached[2]
            schemas, sources = await asyncio.to_thread(self._load, namespace)
            self._cache[namespace] = (time.monotonic() + self._ttl_s, schemas, sources)
            return schemas, sources

    def _load(self, namespace: str) -> tuple[dict[str, str], dict[str, str]]:
        key = self._key(namespace)
        try:
            body = self._client().get_object(Bucket=self._bucket, Key=key)["Body"].read()
        except Exception as exc:  # noqa: BLE001 — a missing/unreadable mapping must not fail the query
            logger.info(
                "mapping_schemas_unavailable",
                namespace=namespace,
                key=key,
                error=type(exc).__name__,
            )
            return {}, {}
        try:
            schemas = table_schemas_from_r2rml(body)
            sources = table_sources_from_r2rml(body)
        except Exception as exc:  # noqa: BLE001 — a malformed mapping must not fail the query
            logger.warning("mapping_schemas_parse_failed", namespace=namespace, error=type(exc).__name__)
            return {}, {}
        logger.info(
            "mapping_schemas_loaded",
            namespace=namespace,
            tables=len(schemas),
            schemas=sorted(set(schemas.values())),
            sources=len(set(sources.values())),
        )
        return schemas, sources

    def invalidate(self, namespace: str | None = None) -> None:
        """Drop the cached map for ``namespace`` (or every namespace when None)."""
        if namespace is None:
            self._cache.clear()
        else:
            self._cache.pop(namespace, None)
