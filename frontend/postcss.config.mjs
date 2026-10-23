// Tailwind runs only where a stylesheet asks for it (components/extend/extend.css, the PDF
// viewer's); every other stylesheet passes through unchanged.
const config = {
  plugins: { "@tailwindcss/postcss": {} },
};

export default config;
