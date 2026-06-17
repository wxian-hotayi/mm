/**
 * Calm "enter latest prices" affordance (DESIGN §20.1 — manual price entry is
 * allowed; it is NOT a quotes/news feed). Shown wherever a widget needs prices
 * to value holdings but none are set. Never fabricates a price or a value.
 */

import { useNavigate } from "react-router-dom";
import { TrendingUp } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";

export interface PricingPromptProps {
  /** Short context line explaining why prices are needed here. */
  description?: string;
}

export function PricingPrompt({ description }: PricingPromptProps) {
  const navigate = useNavigate();
  return (
    <EmptyState
      icon={TrendingUp}
      title="Enter latest prices"
      description={
        description ??
        "Add your broker's latest prices and USD→MYR rate to value holdings."
      }
      action={
        <Button
          variant="outline"
          size="sm"
          onClick={() => navigate("/portfolio")}
        >
          Set latest prices
        </Button>
      }
    />
  );
}
