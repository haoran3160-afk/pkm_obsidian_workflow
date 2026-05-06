export default {
    content: ["./index.html", "./src/**/*.{ts,tsx}"],
    theme: {
        extend: {
            colors: {
                ink: "#181410",
                sand: "#f6efe7",
                ember: "#d66a32",
                pine: "#1f5c4f",
                slate: "#4e6172"
            },
            fontFamily: {
                sans: ["Manrope", "Segoe UI", "sans-serif"],
                mono: ["IBM Plex Mono", "Consolas", "monospace"]
            },
            boxShadow: {
                panel: "0 20px 60px rgba(24, 20, 16, 0.08)"
            }
        }
    },
    plugins: []
};
