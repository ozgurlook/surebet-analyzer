/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: {
          950: "#070c18",
          900: "#0b1329",
          850: "#0f1a38",
          800: "#13224a",
          700: "#1c3168",
          600: "#28448d",
        },
        brand: {
          positive: "#10b981",
          negative: "#f43f5e",
          warning: "#f59e0b",
          primary: "#38bdf8",
        },
      },
      fontFamily: {
        sans: ["Inter", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "SF Mono", "Fira Code", "monospace"],
      },
    },
  },
  plugins: [],
};
