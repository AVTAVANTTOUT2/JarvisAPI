import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        'bg-primary': '#0a0a0f',
        'bg-card': '#12121a',
        'bg-elevated': '#1a1a24',
        'accent-blue': '#4A9EFF',
        'accent-purple': '#9C59FF',
        'accent-green': '#30D158',
        'accent-red': '#FF453A',
        'accent-yellow': '#FFD60A',
        'text-primary': '#FFFFFF',
        'text-secondary': '#888888',
        'text-tertiary': '#555555',
        'text-muted': '#333333',
        'border-subtle': 'rgba(255, 255, 255, 0.07)',
        'border-medium': 'rgba(255, 255, 255, 0.12)',
      },
      borderRadius: {
        'card': '22px',
        'btn': '18px',
        'pill': '20px',
      },
      spacing: {
        'card-padding': '18px',
        'card-gap': '12px',
      },
      fontFamily: {
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          'SF Pro Display',
          'SF Pro Text',
          'Helvetica Neue',
          'Arial',
          'sans-serif',
        ],
      },
    },
  },
  plugins: [],
};

export default config;
