const path = require('path');

/** @type {import('next').NextConfig} */
const config = {
  output: 'export',
  outputFileTracingRoot: path.resolve(__dirname, '..'),
  trailingSlash: true,
  reactStrictMode: true,
  images: { unoptimized: true },
  transpilePackages: ['@jarvis/auth'],
  webpack(webpackConfig) {
    webpackConfig.resolve.symlinks = false;
    webpackConfig.resolve.modules = [
      path.resolve(__dirname, 'node_modules'),
      ...(webpackConfig.resolve.modules || ['node_modules']),
    ];
    webpackConfig.resolve.alias['@desktop'] = path.resolve(__dirname, '../web/src');
    webpackConfig.resolve.alias['@unified'] = path.resolve(__dirname, 'src');
    return webpackConfig;
  },
};

module.exports = config;
