import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Renderer だけを vite でビルドする（file:// で読むので base は相対）
export default defineConfig({
  root: "src/renderer",
  base: "./",
  plugins: [react()],
  build: { outDir: "../../dist/renderer", emptyOutDir: true, target: "chrome130", sourcemap: true },
});
