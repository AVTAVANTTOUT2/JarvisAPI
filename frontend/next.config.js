const path = require('path');

/** @type {import('next').NextConfig} */
const config = {
  output: 'export',
  trailingSlash: true,
  reactStrictMode: true,
  images: { unoptimized: true },
  transpilePackages: ['@jarvis/auth'],
  webpack(webpackConfig, { webpack }) {
    webpackConfig.resolve.symlinks = false;
    webpackConfig.resolve.modules = [
      path.resolve(__dirname, 'node_modules'),
      ...(webpackConfig.resolve.modules || ['node_modules']),
    ];
    webpackConfig.resolve.alias['@desktop'] = path.resolve(__dirname, '../web/src');
    webpackConfig.resolve.alias['@mobile'] = path.resolve(__dirname, '../pwa/src');
    webpackConfig.resolve.alias['@unified'] = path.resolve(__dirname, 'src');
    webpackConfig.plugins.push(new webpack.DefinePlugin({
      'import.meta.env.DEV': JSON.stringify(false),
      'import.meta.env.VITE_API_URL': JSON.stringify(''),
      'import.meta.env.VITE_WS_URL': JSON.stringify(''),
      // Fond de carte MapLibre (OpenFreeMap Dark par défaut — voir web/src/app/lib/mapStyle.ts)
      'import.meta.env.VITE_MAP_STYLE_URL': JSON.stringify(
        process.env.VITE_MAP_STYLE_URL || 'https://tiles.openfreemap.org/styles/dark',
      ),
    }));
    return webpackConfig;
  },
};

module.exports = config;
