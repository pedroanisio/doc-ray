import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useBackupEnabled } from "../api/useBackupEnabled";

type NavItem = { to: string; label: string; end?: boolean; capability?: "backup" };

const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/jobs", label: "Jobs" },
  { to: "/operations", label: "Operations" },
  { to: "/documents", label: "Documents" },
  { to: "/audit", label: "Audit log" },
  { to: "/backups", label: "Backups", capability: "backup" },
  { to: "/identity", label: "Tenants & users" },
  { to: "/billing", label: "Billing" },
];

export function Layout() {
  const { logout } = useAuth();
  const backupEnabled = useBackupEnabled();
  // Capability-gated entries stay hidden until the capability is confirmed, so
  // a disabled deployment never flashes a link that answers 404.
  const nav = NAV.filter((item) => item.capability !== "backup" || backupEnabled === true);
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">doc-ray</div>
        <div className="brand-sub">Admin Console</div>
        <nav>
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => (isActive ? "nav-item active" : "nav-item")}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <button className="logout" onClick={logout}>
          Sign out
        </button>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
