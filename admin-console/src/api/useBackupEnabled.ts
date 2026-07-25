import { useEffect, useState } from "react";
import { useApiClient } from "../services";
import { fetchBackupEnabled } from "./backups";

/**
 * Resolve `Query.backupEnabled` once per mount.
 *
 * `null` means "not yet known" so callers can hold off rendering a decision —
 * flashing a Backups nav entry that then disappears is worse than a beat of
 * nothing. On error we resolve to `false`: if the capability cannot be
 * confirmed, do not advertise it.
 */
export function useBackupEnabled(): boolean | null {
  const client = useApiClient();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  useEffect(() => {
    let active = true;
    fetchBackupEnabled(client)
      .then((value) => active && setEnabled(value))
      .catch(() => active && setEnabled(false));
    return () => {
      active = false;
    };
  }, [client]);
  return enabled;
}
