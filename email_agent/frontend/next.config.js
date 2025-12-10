/** @type {import('next').NextConfig} */
const nextConfig = {
  // Allow connections to backend API
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'http://localhost:8000/:path*',
      },
    ]
  },
  // Experimental features for better streaming support
  experimental: {
    proxyTimeout: 120000, // 2 minutes for streaming
  },
}

module.exports = nextConfig
