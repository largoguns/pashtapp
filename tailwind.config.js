/** Compilado con el binario standalone de Tailwind (sin Node): ver scripts/build_css.sh */
module.exports = {
  content: ["./app/templates/**/*.html", "./app/static/js/**/*.js", "./app/templating.py"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
      },
    },
  },
  plugins: [],
};
