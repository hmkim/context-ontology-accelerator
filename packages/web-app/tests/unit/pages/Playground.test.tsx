// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { vi, describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import React from "react";
import { Playground } from "../../../src/pages/Playground";

// Shared across both mock paths of the hook so the toggle tests below can
// assert what the page hands to sendQuery (the page imports the hook by its
// direct path; the barrel mock is kept for anything that resolves it that way).
const sendQueryMock = vi.hoisted(() => vi.fn());

vi.mock("../../../src/components/RuntimeContext", () => ({
  RuntimeConfigContext: React.createContext({
    region: "us-east-1",
    serveRuntimeArn:
      "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/test",
    oidcConfig: { authority: "https://mock", clientId: "c" },
  }),
}));

vi.mock("../../../src/components/NamespaceProvider", () => ({
  useCurrentNamespace: () => ({
    namespaceId: "ns-1",
    current: { namespaceId: "ns-1", name: "test", displayName: "Test" },
    namespaces: [],
    setNamespace: vi.fn(),
    isLoading: false,
  }),
}));

vi.mock("../../../src/api-hooks", () => ({
  usePlaygroundChatSSE: () => ({
    state: { messages: [] },
    sessionId: null,
    isRestoring: false,
    isStreaming: false,
    sendQuery: sendQueryMock,
    newChat: vi.fn(),
    retryQuery: vi.fn(),
    hasMoreHistory: false,
    isLoadingOlderHistory: false,
    requestOlderHistory: vi.fn(),
  }),
}));

vi.mock("../../../src/api-hooks/use-playground-chat-sse", () => ({
  usePlaygroundChatSSE: () => ({
    state: { messages: [] },
    sessionId: null,
    isRestoring: false,
    isStreaming: false,
    sendQuery: sendQueryMock,
    newChat: vi.fn(),
    retryQuery: vi.fn(),
    hasMoreHistory: false,
    isLoadingOlderHistory: false,
    requestOlderHistory: vi.fn(),
  }),
}));

vi.mock("../../../src/components/SplitPanelProvider", () => ({
  useSplitPanel: () => ({
    state: { content: null, isOpen: false, header: "" },
    openPanel: vi.fn(),
    closePanel: vi.fn(),
  }),
}));

vi.mock("../../../src/components/ToolsPanelProvider", () => ({
  useToolsPanel: () => ({
    state: { drawers: [], activeDrawerId: null },
    registerDrawer: vi.fn(),
    unregisterDrawer: vi.fn(),
    openDrawer: vi.fn(),
    closeDrawer: vi.fn(),
    toggleDrawer: vi.fn(),
  }),
}));

function renderPlayground() {
  return render(
    <MemoryRouter initialEntries={["/playground"]}>
      <Routes>
        <Route path="/playground" element={<Playground />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Playground", () => {
  beforeEach(() => {
    sendQueryMock.mockClear();
  });

  it("renders the playground header", () => {
    renderPlayground();
    expect(screen.getByText("Playground")).toBeInTheDocument();
  });

  it("renders the chat container with empty state", () => {
    renderPlayground();
    expect(
      screen.getByText("Ask a question against the governed surface..."),
    ).toBeInTheDocument();
  });

  function submitQuery(text: string) {
    fireEvent.change(screen.getByRole("textbox", { name: "Query" }), {
      target: { value: text },
    });
    fireEvent.click(screen.getByRole("button", { name: "Run query" }));
  }

  it("does not request compiled artifacts unless the user turns the toggle on", () => {
    renderPlayground();
    const toggle = screen.getByRole("checkbox", {
      name: "Show generated SQL",
    });
    // Off by default: serve's own default withholds the SQL, and the toggle
    // mirrors it rather than silently opting every user in.
    expect(toggle).not.toBeChecked();

    submitQuery("total claims by region");
    expect(sendQueryMock).toHaveBeenCalledTimes(1);
    expect(sendQueryMock).toHaveBeenCalledWith(
      "total claims by region",
      "standard",
      undefined,
      false,
    );
  });

  it("sends includeDebugInfo once Show generated SQL is on", () => {
    renderPlayground();
    fireEvent.click(
      screen.getByRole("checkbox", { name: "Show generated SQL" }),
    );
    expect(
      screen.getByRole("checkbox", { name: "Show generated SQL" }),
    ).toBeChecked();

    submitQuery("show me all claims with amount greater than 10000");
    expect(sendQueryMock).toHaveBeenCalledWith(
      "show me all claims with amount greater than 10000",
      "standard",
      undefined,
      true,
    );
  });
});
