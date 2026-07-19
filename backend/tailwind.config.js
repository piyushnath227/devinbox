/** Tailwind config for the production CSS build. */
module.exports = {
  darkMode: 'class',
  content: ['./app/templates/**/*.html'],
  theme: {
    extend: {
      fontFamily: { sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'] },
      colors: {
        primary: { 400: '#818cf8', 500: '#6366f1', 600: '#4f46e5', 700: '#4338ca' },
        surface: { 700: '#334155', 800: '#1e293b', 900: '#0f172a', 950: '#020617' },
      },
    },
  },
  safelist: [
    'bg-green-900/50', 'text-green-400', 'bg-red-900/50', 'text-red-400',
    'bg-yellow-900/50', 'text-yellow-400', 'bg-primary-600/20', 'text-primary-400',
  ],
};
