/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Static export -> the site is plain HTML/JS/CSS that can be hosted for free
  // on Netlify, GitHub Pages, Cloudflare Pages, S3, etc. (no Node server needed).
  output: 'export',
  images: { unoptimized: true },
};

module.exports = nextConfig;
