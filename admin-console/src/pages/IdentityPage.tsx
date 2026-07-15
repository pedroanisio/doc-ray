import { useState } from "react";
import { useServices } from "../services";
import {
  tenantCreateCommand,
  principalCreateCommand,
  IdentityApiUnavailableError,
  type Role,
  type PrincipalKind,
} from "../api/identity";
import { PageHeader } from "../components/common";

/**
 * Tenants & users. The doc-ray backend has NO HTTP endpoint for tenant/user/role
 * administration yet — it is CLI-only (auth_admin.py). Rather than wire buttons
 * to routes that 404, this page:
 *   1. builds the exact CLI command an operator runs today (live preview), and
 *   2. proves the gap by calling the IdentityProvider, which rejects with the
 *      documented IdentityApiUnavailableError.
 * When backend mutations land, swap in an HttpIdentityProvider and this page's
 * submit handlers light up unchanged.
 */
export function IdentityPage() {
  const { identity } = useServices();

  const [slug, setSlug] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [pName, setPName] = useState("");
  const [pKind, setPKind] = useState<PrincipalKind>("human");
  const [pTenant, setPTenant] = useState("");
  const [pRoles, setPRoles] = useState<Role[]>(["reader"]);
  const [result, setResult] = useState<{ cmd: string; note: string } | null>(null);

  async function attemptTenant() {
    try {
      await identity.createTenant({ slug, displayName });
      // If a real provider ever succeeds, we simply report success.
      setResult({ cmd: "", note: "Tenant created via API." });
    } catch (err) {
      if (err instanceof IdentityApiUnavailableError) {
        setResult({ cmd: err.cliCommand, note: "Run this on a host with the doc-ray CLI:" });
      } else {
        setResult({ cmd: "", note: err instanceof Error ? err.message : String(err) });
      }
    }
  }

  async function attemptPrincipal() {
    try {
      await identity.createPrincipal({
        displayName: pName,
        kind: pKind,
        tenant: pTenant || undefined,
        roles: pRoles,
      });
      setResult({ cmd: "", note: "Principal created via API." });
    } catch (err) {
      if (err instanceof IdentityApiUnavailableError) {
        setResult({ cmd: err.cliCommand, note: "Run this on a host with the doc-ray CLI:" });
      } else {
        setResult({ cmd: "", note: err instanceof Error ? err.message : String(err) });
      }
    }
  }

  function toggleRole(role: Role) {
    setPRoles((cur) => (cur.includes(role) ? cur.filter((r) => r !== role) : [...cur, role]));
  }

  return (
    <div>
      <PageHeader title="Tenants & users" />

      <p role="note" className="alert warn">
        Tenant, user, and role administration currently has <strong>no HTTP API</strong> in doc-ray
        — it is CLI-driven (<span className="mono">auth_admin.py</span>) plus IdP provisioning
        (SCIM/OIDC/SAML). This screen generates the exact CLI command to run today and is wired to a
        typed <span className="mono">IdentityProvider</span> interface, so it becomes fully
        interactive the moment backend mutations exist — no UI rewrite.
      </p>

      <section className="card">
        <h2>Create tenant</h2>
        <div className="form-grid">
          <label>Slug<input value={slug} onChange={(e) => setSlug(e.target.value)} /></label>
          <label>
            Display name
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </label>
        </div>
        <pre className="cli-preview">{tenantCreateCommand({ slug: slug || "<slug>", displayName: displayName || "<name>" })}</pre>
        <button onClick={attemptTenant} disabled={!slug || !displayName}>
          Provision tenant
        </button>
      </section>

      <section className="card">
        <h2>Create principal (user / service)</h2>
        <div className="form-grid">
          <label>
            Display name
            <input value={pName} onChange={(e) => setPName(e.target.value)} />
          </label>
          <label>
            Kind
            <select value={pKind} onChange={(e) => setPKind(e.target.value as PrincipalKind)}>
              <option value="human">human</option>
              <option value="service">service</option>
            </select>
          </label>
          <label>
            Tenant (slug)
            <input value={pTenant} onChange={(e) => setPTenant(e.target.value)} />
          </label>
          <fieldset className="roles">
            <legend>Roles</legend>
            {(["reader", "curator", "admin"] as Role[]).map((r) => (
              <label key={r} className="role-check">
                <input type="checkbox" checked={pRoles.includes(r)} onChange={() => toggleRole(r)} />
                {r}
              </label>
            ))}
          </fieldset>
        </div>
        <pre className="cli-preview">
          {principalCreateCommand({
            displayName: pName || "<name>",
            kind: pKind,
            tenant: pTenant || undefined,
            roles: pRoles,
          })}
        </pre>
        <button onClick={attemptPrincipal} disabled={!pName || pRoles.length === 0}>
          Provision principal
        </button>
      </section>

      {result && (
        <section className="card result">
          <h3>{result.note}</h3>
          {result.cmd && <pre className="cli-preview run">{result.cmd}</pre>}
        </section>
      )}
    </div>
  );
}
