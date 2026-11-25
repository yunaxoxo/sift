import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run build` writes straight into the Python package, where `sift ui`
// serves it. `npm run dev` proxies the JSON API to a running `sift ui`.
// Fonts are otherwise found only after the stylesheet is parsed. Preload the three faces the
// first screen uses (not the italic, which only block quotes need), under their hashed names.
const ABOVE_THE_FOLD = /(newsreader|plus-jakarta-sans|jetbrains-mono)-latin-wght-normal.*\.woff2$/;
const preloadFonts = {
  name: "preload-fonts",
  transformIndexHtml: {
    order: "post" as const,
    handler(_html: string, ctx: { bundle?: Record<string, unknown> }) {
      return Object.keys(ctx.bundle ?? {})
        .filter((file) => ABOVE_THE_FOLD.test(file))
        .map((file) => ({
          tag: "link",
          injectTo: "head" as const,
          attrs: { rel: "preload", as: "font", type: "font/woff2", crossorigin: "", href: `./${file}` },
        }));
    },
  },
};

export default defineConfig({
  plugins: [react(), preloadFonts],
  base: "./",
  build: {
    outDir: "../sift/ui/static",
    emptyOutDir: true,
    sourcemap: false,
    assetsInlineLimit: 0, // the server's CSP has no `data:` fonts or styles
  },
  server: {
    port: 5173,
    fs: { allow: [".."] }, // the offline fallback imports sift/ui/preview.json
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
});
