/**
 * Identity administration — tenants, principals (users/services), roles, ACLs.
 *
 * Known gap: there is no GraphQL or REST endpoint for tenant CRUD, principal
 * creation, role assignment, or document ACL grants. These operations exist
 * only in the CLI `src/doc_ray/entrypoints/auth_admin.py` (plus IdP-driven
 * provisioning via SCIM/OIDC/SAML). A button that POSTs to a route returning
 * 404 is worse than an honest "not yet", so no client call is fabricated here.
 *
 * This module therefore defines the FUTURE interface (so the UI is written
 * against a stable contract) and ships a default implementation that refuses
 * every call with a documented error carrying the exact CLI command an operator
 * runs today. When backend mutations land, add an `HttpIdentityProvider`
 * implementing the same interface and swap the binding — no UI rewrite needed.
 */

export type Role = "reader" | "curator" | "admin";
export type Permission = "read" | "annotate" | "delete" | "redact" | "admin";
export type PrincipalKind = "human" | "service";

export interface Tenant {
  id: string;
  slug: string;
  displayName: string;
  disabledAt: string | null;
}

export interface Principal {
  id: string;
  displayName: string;
  kind: PrincipalKind;
  tenant: string | null;
  roles: Role[];
}

export interface CreateTenantInput {
  slug: string;
  displayName: string;
}

export interface CreatePrincipalInput {
  displayName: string;
  kind: PrincipalKind;
  tenant?: string;
  roles: Role[];
  externalSubject?: string;
}

export interface GrantAclInput {
  principal: string;
  documentId: string;
  permission: Permission;
}

/** The contract the UI codes against; two implementations may satisfy it. */
export interface IdentityProvider {
  createTenant(input: CreateTenantInput): Promise<Tenant>;
  createPrincipal(input: CreatePrincipalInput): Promise<Principal>;
  grantDocumentAcl(input: GrantAclInput): Promise<void>;
}

const CLI = "python -m doc_ray.entrypoints.auth_admin";

export function tenantCreateCommand(input: CreateTenantInput): string {
  return `${CLI} tenant create --slug ${input.slug} --display-name '${input.displayName}'`;
}

export function principalCreateCommand(input: CreatePrincipalInput): string {
  const parts = [
    `${CLI} principal create`,
    `--display-name '${input.displayName}'`,
    `--kind ${input.kind}`,
  ];
  if (input.tenant) parts.push(`--tenant ${input.tenant}`);
  if (input.externalSubject) parts.push(`--external-subject ${input.externalSubject}`);
  for (const role of input.roles) parts.push(`--role ${role}`);
  return parts.join(" ");
}

export function grantDocumentCommand(input: GrantAclInput): string {
  return (
    `${CLI} grant document --principal ${input.principal} ` +
    `--document-id ${input.documentId} --permission ${input.permission}`
  );
}

export class IdentityApiUnavailableError extends Error {
  readonly operation: string;
  readonly cliCommand: string;
  constructor(operation: string, cliCommand: string) {
    super(
      `"${operation}" has no HTTP endpoint in doc-ray yet. Run it via the CLI instead:\n  ${cliCommand}`,
    );
    this.name = "IdentityApiUnavailableError";
    this.operation = operation;
    this.cliCommand = cliCommand;
  }
}

export class UnavailableIdentityProvider implements IdentityProvider {
  createTenant(input: CreateTenantInput): Promise<Tenant> {
    return Promise.reject(
      new IdentityApiUnavailableError("createTenant", tenantCreateCommand(input)),
    );
  }

  createPrincipal(input: CreatePrincipalInput): Promise<Principal> {
    return Promise.reject(
      new IdentityApiUnavailableError("createPrincipal", principalCreateCommand(input)),
    );
  }

  grantDocumentAcl(input: GrantAclInput): Promise<void> {
    return Promise.reject(
      new IdentityApiUnavailableError("grantDocumentAcl", grantDocumentCommand(input)),
    );
  }
}
