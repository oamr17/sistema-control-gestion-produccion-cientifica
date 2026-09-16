import type { ReactNode } from "react";

import type { B2BAction, Capabilities } from "../../lib/human-review";

export function CapabilityGate({
  action,
  capabilities,
  isLoading = false,
  error = null,
  children
}: {
  action: B2BAction;
  capabilities?: Capabilities;
  isLoading?: boolean;
  error?: unknown;
  children: ReactNode;
}) {
  if (isLoading || error || !capabilities?.actions.includes(action)) return null;
  return <>{children}</>;
}
