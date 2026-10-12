/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#1d1d1f",
        mist: "#86868b",
        line: "#e8e8ed",
        paper: "#f5f5f7",
        teal: "#1d1d1f",
        navy: "#1d1d1f",
      },
      fontFamily: {
        sans: ["Inter", "-apple-system", "BlinkMacSystemFont", "Helvetica Neue", "sans-serif"],
        serif: ["Inter", "-apple-system", "BlinkMacSystemFont", "Helvetica Neue", "sans-serif"],
      },
      boxShadow: {
        card: "0 1px 3px rgba(0, 0, 0, 0.06), 0 1px 2px rgba(0, 0, 0, 0.04)",
      },
    },
  },
  plugins: [],
};
