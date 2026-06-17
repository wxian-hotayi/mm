import { Construction } from "lucide-react";

import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";

export interface PlaceholderPageProps {
  title: string;
  /** Short note about what this surface will become. */
  note?: string;
}

/**
 * Foundation-stage route placeholder. Lets the AppShell render a complete,
 * navigable shell BEFORE the Pages stage exists (Phase 3 completion criterion).
 * Each real page replaces its placeholder in the Pages stage. Renders no
 * financial data and makes no recommendation (DESIGN §20.0).
 */
export function PlaceholderPage({ title, note }: PlaceholderPageProps) {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader title={title} description="Behavioral Interface Layer" />
      <EmptyState
        icon={Construction}
        title={`${title} — coming in the Pages stage`}
        description={
          note ??
          "The foundation (layout, UI kit, API client, state) is ready; this surface is built next."
        }
      />
    </div>
  );
}
