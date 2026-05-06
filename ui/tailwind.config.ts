import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#1e1a16",
        parchment: "#f5efe6",
        sand: "#e7d9c8",
        chalk: "#fffdfa",
        ember: "#c96a3d",
        pine: "#236a57",
        slate: "#5f6f73",
        fog: "#cabaa8"
      },
      fontFamily: {
        sans: ["Manrope", "sans-serif"],
        display: ["Fraunces", "Georgia", "serif"],
        mono: ["IBM Plex Mono", "Consolas", "monospace"]
      },
      boxShadow: {
        panel: "0 24px 80px rgba(30, 26, 22, 0.09)",
        inset: "inset 0 1px 0 rgba(255, 255, 255, 0.65)"
      },
      backgroundImage: {
        noise:
          "radial-gradient(circle at top left, rgba(201,106,61,0.18), transparent 28%), radial-gradient(circle at 80% 0%, rgba(35,106,87,0.12), transparent 24%), linear-gradient(180deg, #f5efe6 0%, #fffdfa 100%)"
      }
    }
  },
  plugins: []
} satisfies Config;
