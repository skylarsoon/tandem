import type { NextConfig } from "next";
const integration = process.env.TANDEM_NEXT_DIST_DIR === ".next-integration";
const config: NextConfig = {
  distDir: integration ? ".next-integration" : ".next",
  typescript: {
    tsconfigPath: integration ? "tsconfig.integration.json" : "tsconfig.json",
  },
};
export default config;
