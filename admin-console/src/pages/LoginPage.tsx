import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

/**
 * Operator sign-in. Posts to POST /auth/login (requires DOCRAY_AUTH_LOCAL_ENABLED
 * on the API). SSO (OIDC/SAML) is handled by the backend redirect flows and is
 * out of scope for this shell.
 */
export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(email, password);
      navigate("/", { replace: true });
    } catch {
      // The API deliberately returns an opaque 401 for bad credentials; we do
      // not echo server text to avoid leaking which half was wrong.
      setError("Sign-in failed. Check your credentials and that local auth is enabled.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-shell">
      <form className="card login-card" onSubmit={onSubmit}>
        <h1>doc-ray · Admin Console</h1>
        <p className="muted">Operator sign-in</p>
        {error && (
          <p role="alert" className="alert">
            {error}
          </p>
        )}
        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <button type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
