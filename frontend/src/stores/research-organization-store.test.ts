import { beforeEach, describe, expect, it } from "vitest";

import { useResearchOrganizationStore } from "./research-organization-store";

describe("Research organization store", () => {
  beforeEach(() => {
    useResearchOrganizationStore.setState({ activeOrganizationId: null });
  });

  it("defaults to the personal research space", () => {
    expect(useResearchOrganizationStore.getState().activeOrganizationId).toBeNull();
  });

  it("switches organization context and can return to personal", () => {
    useResearchOrganizationStore.getState().setActiveOrganizationId("org-a");
    expect(useResearchOrganizationStore.getState().activeOrganizationId).toBe("org-a");

    useResearchOrganizationStore.getState().setActiveOrganizationId(null);
    expect(useResearchOrganizationStore.getState().activeOrganizationId).toBeNull();
  });
});
