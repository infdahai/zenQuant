import type { GlobalThemeOverrides } from "naive-ui";

export const themeOverrides: GlobalThemeOverrides = {
  common: {
    bodyColor: "#0a0e14",
    cardColor: "#111820",
    modalColor: "#111820",
    popoverColor: "#162030",
    borderColor: "rgba(55,90,120,0.25)",
    dividerColor: "rgba(55,90,120,0.20)",
    textColorBase: "#e0e6ed",
    textColor1: "#e0e6ed",
    textColor2: "#a0b4c8",
    textColor3: "#6b7f94",
    primaryColor: "#14d9b4",
    primaryColorHover: "#18f0c8",
    primaryColorPressed: "#10c0a0",
    warningColor: "#f5a524",
    warningColorHover: "#f7b84a",
    errorColor: "#ff4757",
    errorColorHover: "#ff6b7a",
    successColor: "#14d9b4",
    infoColor: "#4a90d9",
    fontSize: "13px",
    fontFamily:
      "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    fontFamilyMono: "'JetBrains Mono', 'Fira Code', monospace",
    borderRadius: "6px",
  },
  Card: {
    color: "#111820",
    borderColor: "rgba(55,90,120,0.25)",
  },
  Tag: {
    borderRadius: "4px",
  },
  DataTable: {
    thColor: "#0d1218",
    tdColor: "#111820",
    borderColor: "rgba(55,90,120,0.15)",
  },
};

// Semantic color tokens for trading-specific UI
export const colors = {
  bg: "#0a0e14",
  surface: "#111820",
  surfaceHover: "#162030",
  border: "rgba(55,90,120,0.25)",
  text: "#e0e6ed",
  textMuted: "#6b7f94",
  textSecondary: "#a0b4c8",
  ok: "#14d9b4",
  warning: "#f5a524",
  danger: "#ff4757",
  long: "#14d9b4",
  short: "#ff4757",
  flat: "#6b7f94",
  candleUp: "#14d9b4",
  candleDown: "#ff4757",
} as const;
