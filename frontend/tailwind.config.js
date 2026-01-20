/** @type {import('tailwindcss').Config} */
export default {
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
        extend: {
            colors: {
                brand: {
                    dark: "#0f172a",
                    accent: "#38bdf8",
                    card: "#1e293b",
                }
            }
        },
    },
    plugins: [],
}
