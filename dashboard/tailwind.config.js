/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        background: '#F6F8FB',
        surface: '#FFFFFF',
        border: '#E3E8EF',
        text: '#0B1B33',
        muted: '#5B6B82',
        accent: '#0A6CFF',
        navy: '#002E6E',
        verified: '#0E9F6E',
        awaiting: '#D97706',
        blocked: '#DC2626',
      },
    },
  },
  plugins: [],
}
