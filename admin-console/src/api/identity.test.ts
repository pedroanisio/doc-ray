import { describe, it, expect } from "vitest";
import {
  UnavailableIdentityProvider,
  IdentityApiUnavailableError,
  tenantCreateCommand,
  principalCreateCommand,
  grantDocumentCommand,
} from "./identity";

describe("CLI command builders (the real path for identity ops today)", () => {
  it("builds a tenant create command", () => {
    expect(tenantCreateCommand({ slug: "acme", displayName: "Acme Inc" })).toBe(
      "python -m doc_ray.entrypoints.auth_admin tenant create --slug acme --display-name 'Acme Inc'",
    );
  });

  it("builds a principal create command with repeated --role flags", () => {
    const cmd = principalCreateCommand({
      displayName: "Ops Bot",
      kind: "service",
      tenant: "acme",
      roles: ["curator", "admin"],
    });
    expect(cmd).toContain("principal create --display-name 'Ops Bot'");
    expect(cmd).toContain("--kind service");
    expect(cmd).toContain("--tenant acme");
    expect(cmd).toContain("--role curator");
    expect(cmd).toContain("--role admin");
  });

  it("builds a document ACL grant command", () => {
    expect(
      grantDocumentCommand({ principal: "p-1", documentId: "d-9", permission: "read" }),
    ).toBe(
      "python -m doc_ray.entrypoints.auth_admin grant document --principal p-1 --document-id d-9 --permission read",
    );
  });
});

describe("UnavailableIdentityProvider", () => {
  const provider = new UnavailableIdentityProvider();

  it("rejects createTenant with a documented error carrying the CLI fallback", async () => {
    await expect(
      provider.createTenant({ slug: "acme", displayName: "Acme Inc" }),
    ).rejects.toBeInstanceOf(IdentityApiUnavailableError);

    const err = await provider.createTenant({ slug: "acme", displayName: "Acme Inc" }).catch((e) => e);
    expect(err.operation).toBe("createTenant");
    expect(err.cliCommand).toContain("tenant create --slug acme");
    expect(err.message).toMatch(/no HTTP endpoint/i);
  });

  it("rejects createPrincipal and grantDocumentAcl the same way", async () => {
    await expect(
      provider.createPrincipal({ displayName: "X", kind: "human", roles: ["reader"] }),
    ).rejects.toBeInstanceOf(IdentityApiUnavailableError);
    await expect(
      provider.grantDocumentAcl({ principal: "p", documentId: "d", permission: "admin" }),
    ).rejects.toBeInstanceOf(IdentityApiUnavailableError);
  });

  it("has no list operation, because no read endpoint exists either", () => {
    // Guard against a future edit quietly adding a fabricated listTenants().
    expect((provider as unknown as Record<string, unknown>).listTenants).toBeUndefined();
  });
});
