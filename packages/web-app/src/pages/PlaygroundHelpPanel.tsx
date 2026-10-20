// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import HelpPanel from "@cloudscape-design/components/help-panel";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Link from "@cloudscape-design/components/link";

export const PlaygroundHelpPanel: React.FC = () => (
  <HelpPanel
    header={<h2>Playground</h2>}
    footer={
      <>
        <h3>Learn more</h3>
        <SpaceBetween size="xxs">
          <Link
            external
            href="https://github.com/aws/context-ontology-accelerator"
          >
            Context Ontology Accelerator on GitHub
          </Link>
        </SpaceBetween>
      </>
    }
  >
    <p>
      Ask a business question in plain language. Context Ontology Accelerator
      routes through three tiers — cheapest first — and falls through gracefully
      when a tier can't answer.
    </p>

    <h3>Three-tier resolution</h3>
    <ul>
      <li>
        <strong>Tier 1 — metric.</strong> Synonym table → pre-compiled SQL →
        Athena. Zero LLM calls. Deterministic; the number a CFO sees in a
        dashboard equals the number an agent returns over MCP.
      </li>
      <li>
        <strong>Tier 2 — ontology.</strong> NL → SPARQL (LLM) → VKG → SQL →
        Athena. Grounded in the formal data model, not table-name guessing.
      </li>
      <li>
        <strong>Tier 3 — knowledge.</strong> Parallel vector + graph retrieval →
        LLM synthesis. Always cites which documents informed the answer.
      </li>
    </ul>

    <h3>Boundary controls (cross-cutting)</h3>
    <ul>
      <li>
        <strong>Cedar</strong> — authorize on (principal, action, resource)
        before any tier runs.
      </li>
      <li>
        <strong>SQL Firewall</strong> — rewrites compiled SQL with Cedar-derived
        row filters.
      </li>
      <li>
        <strong>Bedrock Guardrails</strong> — content filter on prompts and
        outputs.
      </li>
    </ul>

    <h3>Demos to try</h3>
    <ul>
      <li>
        <strong>Demo 1.</strong> "total claims by region" — Tier 1
        deterministic. Then "claims count" — same metric via synonym match.
      </li>
      <li>
        <strong>Demo 2.</strong> "show me all claims with amount greater than
        10000" — Tier 2 with compiled SPARQL + SQL drawer. Turn on{" "}
        <em>Show generated SQL</em> first: serve returns the executed SQL and
        SPARQL only when the request opts in, because they expose table and
        column names.
      </li>
      <li>
        <strong>Demo 2 fallback.</strong> "list all customers in northeast
        region" — Tier 2 translates fine, Athena table is missing, system falls
        to Tier 3 with no error.
      </li>
      <li>
        <strong>Demo 3.</strong> "what is the claims filing process" — Tier 3
        markdown answer with cited chunks. Then "explain underwriting
        guidelines" — Tier 3 honest refusal at low confidence.
      </li>
      <li>
        <strong>Demo 4.</strong> Click <em>Re-run as Tier 3</em> on a Tier 1
        answer. Tabs appear; structurally different answers.
      </li>
      <li>
        <strong>Demo 5.</strong> Submit an empty query or a namespace with
        special characters → fail-fast validation error in &lt;100ms.
      </li>
      <li>
        <strong>Demo 6.</strong> Every answer has a request-ID chip — copy it to
        follow the request through the rationale drawer and CloudWatch.
      </li>
    </ul>
  </HelpPanel>
);
