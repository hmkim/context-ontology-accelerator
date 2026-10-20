// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Request-shaping for the Playground's `options` object.
 *
 * The Playground page test mocks `usePlaygroundChatSSE` wholesale, so without
 * these the request shape has no coverage at all — and a regression here would
 * silently drop `mode`, which worked before `strategy` existed, not just the new
 * field.
 */
import { describe, it, expect } from "vitest";
import { buildOptions } from "./use-playground-chat-sse";

describe("buildOptions", () => {
  it("omits options entirely when nothing is set", () => {
    // Spreading `{}` adds no key, so the request is byte-identical to one built
    // before this field existed.
    expect(buildOptions()).toEqual({});
  });

  it("sends mode alone, as it did before strategy existed", () => {
    expect(buildOptions("deep-reasoning")).toEqual({
      options: { mode: "deep-reasoning" },
    });
  });

  it("sends strategy alone", () => {
    expect(buildOptions(undefined, "ontop")).toEqual({
      options: { strategy: "ontop" },
    });
  });

  it("sends both without one clobbering the other", () => {
    // The reason this is a helper rather than two conditional spreads: two
    // spreads of `options` would leave only the second.
    expect(buildOptions("standard", "ontop_first")).toEqual({
      options: { mode: "standard", strategy: "ontop_first" },
    });
  });

  it("treats an empty string as unset rather than sending it", () => {
    // Guards the falsy-check: `""` must not reach the API as a pin, which serve
    // would fail to match and silently resolve to its default.
    expect(buildOptions("" as never, "" as never)).toEqual({});
  });

  it("sends includeDebugInfo only when true", () => {
    // Serve withholds queryUsed / sparqlGenerated unless the caller opts in, so
    // the flag must reach the API as a literal `true` ...
    expect(buildOptions("standard", undefined, true)).toEqual({
      options: { mode: "standard", includeDebugInfo: true },
    });
    // ... and alone it still produces an options object.
    expect(buildOptions(undefined, undefined, true)).toEqual({
      options: { includeDebugInfo: true },
    });
  });

  it("omits includeDebugInfo when false or unset, keeping the old request shape", () => {
    // `false` is the serve default; sending it would change the wire shape of
    // every request for no behavioural gain, so the key is dropped instead.
    expect(buildOptions("standard", undefined, false)).toEqual({
      options: { mode: "standard" },
    });
    expect(buildOptions(undefined, undefined, false)).toEqual({});
    expect(buildOptions("deep-reasoning", "ontop", undefined)).toEqual({
      options: { mode: "deep-reasoning", strategy: "ontop" },
    });
  });
});
