<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { t } from '../i18n'
</script>

<template>
  <div class="page page--narrow">
    <h1>{{ t('nf.title') }}</h1>
    <p><RouterLink to="/">{{ t('nf.back') }}</RouterLink></p>
  </div>
</template>
