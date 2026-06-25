import { useTranslation } from 'react-i18next'
import { SettingsForm } from '@/components/app/settings-form'
import { PageShell } from '@/components/app/shared-ui'

export function SettingsPage() {
  const { t } = useTranslation()

  return (
    <PageShell title={t('settings.title')} description={t('settings.description')}>
      <SettingsForm />
    </PageShell>
  )
}
