/** @type {import('next').NextConfig} */
const nextConfig = {
  output: process.env.NEXT_STANDALONE === "1" ? "standalone" : undefined,
  experimental: {
    webpackBuildWorker: false,
    workerThreads: true
  },
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "images.unsplash.com"
      }
    ]
  }
};

module.exports = nextConfig;
