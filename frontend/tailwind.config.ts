import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}"
  ],
  theme: {
    extend: {
      colors: {
        ink: "#111827",
        paper: "#F4F9FD",
        mint: "#005695",
        coral: "#D7354A",
        amber: "#F2C94C",
        grape: "#86B9E2",
        line: "#D8EAF7"
      },
      boxShadow: {
        soft: "0 18px 45px rgba(17, 24, 39, 0.08)"
      }
    }
  },
  plugins: []
};

export default config;
