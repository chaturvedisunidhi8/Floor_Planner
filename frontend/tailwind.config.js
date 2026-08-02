/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          50: '#f6f6f5',
          100: '#e7e7e4',
          200: '#cfcfc9',
          300: '#adaca3',
          400: '#84837a',
          500: '#6a695f',
          600: '#54534b',
          700: '#44433e',
          800: '#3a3935',
          900: '#33322f',
          950: '#1a1a18',
        },
        blueprint: {
          50: '#f0f7f7',
          100: '#daebea',
          200: '#b9d8d7',
          300: '#8bbdbc',
          400: '#589b9b',
          500: '#3d8081',
          600: '#2f6667',
          700: '#295354',
          800: '#254445',
          900: '#223a3b',
          950: '#0f2122',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 2px rgba(26,26,24,0.04), 0 8px 24px -12px rgba(26,26,24,0.18)',
        lift: '0 2px 4px rgba(26,26,24,0.06), 0 16px 40px -16px rgba(26,26,24,0.28)',
      },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'fade-up': 'fade-up 0.35s ease-out both',
      },
    },
  },
  plugins: [],
};
