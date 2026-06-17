import { useNavigate } from "react-router-dom";

import { Drawer } from "@/components/ui/Drawer";
import { cn } from "@/lib/utils";
import { MORE_NAV_ITEMS } from "@/lib/constants";
import { useUiStore } from "@/stores/ui";

/** Mobile secondary-navigation bottom sheet (items not in the bottom nav). */
export function MoreDrawer() {
  const open = useUiStore((s) => s.moreDrawerOpen);
  const close = useUiStore((s) => s.closeMoreDrawer);
  const navigate = useNavigate();

  return (
    <Drawer open={open} onClose={close} title="More">
      <div className="grid grid-cols-2 gap-2">
        {MORE_NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.to}
              type="button"
              onClick={() => {
                close();
                navigate(item.to);
              }}
              className={cn(
                "flex items-center gap-3 rounded-xl border border-border bg-surface2 px-3 py-3 text-left text-sm font-medium text-text",
                "active:bg-border",
              )}
            >
              <Icon className="h-5 w-5 text-muted" />
              {item.label}
            </button>
          );
        })}
      </div>
    </Drawer>
  );
}
