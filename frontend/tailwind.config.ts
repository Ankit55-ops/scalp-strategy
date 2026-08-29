import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0f14",
        panel: "#11161d",
        panel2: "#161d26",
        border: "#232c38",
        accent: "#2dd4bf",
        danger: "#f87171",
        warn: "#fbbf24",
        text: {
          DEFAULT: "#d5dde6",
          dim: "#8b98a7",
        },
      },
    },
  },
  plugins: [],
};

export default config;