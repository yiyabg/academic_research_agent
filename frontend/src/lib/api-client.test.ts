import { describe, expect, it } from "vitest";

import { ApiError, apiErrorMessage } from "./api-client";

describe("apiErrorMessage", () => {
  it("unwraps the FastAPI rate-limit error envelope into display text", () => {
    const payload = {
      detail: {
        error: {
          code: "RATE_LIMIT_EXCEEDED",
          message: "Rate limit exceeded. Retry after 60 seconds.",
        },
      },
    };

    expect(apiErrorMessage(payload)).toBe("Rate limit exceeded. Retry after 60 seconds.");
    expect(new ApiError(429, payload).message).toBe("Rate limit exceeded. Retry after 60 seconds.");
  });

  it("never returns an object for a malformed response", () => {
    expect(apiErrorMessage({ detail: { unexpected: true } })).toBe("Request failed");
    expect(apiErrorMessage([{ msg: "invalid email" }])).toBe("Request failed");
  });
});
