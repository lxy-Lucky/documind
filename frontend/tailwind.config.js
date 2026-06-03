/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{vue,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          deep: "#0a0b0f",
          main: "#101117",
          surface: "#171822",
          elevated: "#1e2030",
          hover: "#252840",
        },
        accent: {
          DEFAULT: "#e4a853",
          dim: "#c48a3a",
        },
        green: { DEFAULT: "#5ad4a6" },
        red: { DEFAULT: "#e85d6f" },
        blue: { DEFAULT: "#5b9df0" },
        purple: { DEFAULT: "#a78bfa" },
        text1: "#eae6de",
        text2: "#9e9a92",
        text3: "#5c5953",
        border: { DEFAULT: "#252738", light: "#2f3248" },
      },
      fontFamily: {
        d: ['Syne', 'Noto Serif SC', 'serif'],
        b: ['Noto Serif SC', 'serif'],
        m: ['JetBrains Mono', 'monospace'],
      },
      boxShadow: {
        glow: "0 2px 14px rgba(228,168,83,0.22)",
        soft: "0 0 28px rgba(228,168,83,0.12)",
      },
      borderRadius: {
        DEFAULT: "10px",
        sm: "7px",
      },
    },
  },
  plugins: [],
};
