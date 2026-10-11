import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Gateway が /admin で配る（同じオリジンの /admin/v1 と /v1/auth を呼ぶ）
export default defineConfig({
  root: "src",
  base: "/admin/",
  plugins: [react()],
  build: { outDir: "../dist", emptyOutDir: true, target: "es2022" },
});
