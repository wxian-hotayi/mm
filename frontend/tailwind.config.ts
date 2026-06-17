import type { Config } from "tailwindcss";

// WealthOS dark theme tokens (DESIGN §10). Dark is the only theme in v1.
const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: "#0A0E14",
        surface: "#111722",
        surface2: "#1A2230",
        border: "#232D3F",
        text: "#E6EAF2",
        muted: "#8B94A7",
        accent: {
          DEFAULT: "#6366F1",
          fg: "#EEF0FF",
          muted: "#4F46E5",
        },
        gain: "#10B981",
        loss: "#F87171",
        warn: "#F59E0B",
      },
      fontFamily: {
        sans: [
          "Inter",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
      },
      borderRadius: {
        xl: "0.875rem",
        "2xl": "1.125rem",
      },
      boxShadow: {
        card: "0 1px 2px rgba(0, 0, 0, 0.4), 0 8px 24px rgba(0, 0, 0, 0.28)",
        pop: "0 10px 40px rgba(0, 0, 0, 0.55)",
      },
      maxWidth: {
        "screen-sm": "640px",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        "scale-in": {
          from: { opacity: "0", transform: "scale(0.96)" },
          to: { opacity: "1", transform: "scale(1)" },
        },
        "slide-up": {
          from: { transform: "translateY(100%)" },
          to: { transform: "translateY(0)" },
        },
        spin: {
          to: { transform: "rotate(360deg)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.15s ease-out",
        "scale-in": "scale-in 0.15s ease-out",
        "slide-up": "slide-up 0.22s ease-out",
        spin: "spin 0.7s linear infinite",
      },
    },
  },
  plugins: [],
};

export default config;
