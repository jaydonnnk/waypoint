/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // S10 (ADR 0007): the container build sets NEXT_STANDALONE=1 to get the
  // self-contained standalone server; local dev/build keep the default.
  output: process.env.NEXT_STANDALONE === "1" ? "standalone" : undefined,
};

export default nextConfig;
