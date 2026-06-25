import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import zh from './locales/zh.json'
import en from './locales/en.json'

const LANGUAGE_KEY = 'media-pilot-language'

function detectLanguage(): string {
  try {
    const stored = localStorage.getItem(LANGUAGE_KEY)
    if (stored === 'en' || stored === 'zh') return stored
  } catch { /* noop */ }
  return 'zh'
}

i18n.use(initReactI18next).init({
  resources: { zh: { translation: zh }, en: { translation: en } },
  lng: detectLanguage(),
  fallbackLng: 'zh',
  interpolation: { escapeValue: false },
})

export { LANGUAGE_KEY }
export default i18n
