import { afterEach, describe, expect, it } from "vitest";

import { createClientId, createUuid } from "./client-id";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const originalCrypto = globalThis.crypto;

afterEach(() => {
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: originalCrypto });
});

describe("client id generation", () => {
  it("generates a UUID when randomUUID is unavailable on an HTTP LAN origin", () => {
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: undefined });

    expect(createUuid()).toMatch(UUID_V4);
    expect(createClientId("analysis")).toMatch(/^analysis-/);
  });
});
