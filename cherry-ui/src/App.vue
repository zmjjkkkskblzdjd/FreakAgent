<script setup>
import { watch } from 'vue'
import { useSettingsStore } from '@/stores/settings'

const settings = useSettingsStore()


watch(
  () => settings.theme,
  (theme) => {
    const html = document.documentElement
    html.classList.remove('light', 'dark')
    html.classList.add(theme)
  },
  { immediate: true }
)
</script>

<template>
  <router-view />
</template>
