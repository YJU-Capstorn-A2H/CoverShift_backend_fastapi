<script setup lang="ts">
// 제출 현황: 직원별로 종류마다 몇 날을 냈는지. 미제출 표시 (G19 의 뜻을 같이 적는다).
import { computed } from 'vue'
import { t } from '../i18n'
import type { EmployeeSubmissions } from '../types'

const props = defineProps<{ submissions: readonly EmployeeSubmissions[] }>()

const rows = computed(() =>
  props.submissions.map((s) => ({
    ...s,
    available: s.items.filter((i) => i.kind === 'available').length,
    off: s.items.filter((i) => i.kind === 'off').length,
    paid: s.items.filter((i) => i.kind === 'paid_leave').length,
    half: s.items.filter((i) => i.kind === 'half_leave').length,
  })),
)
const submitted = computed(() => rows.value.filter((r) => r.items.length > 0).length)
</script>

<template>
  <section class="section" aria-labelledby="sub-title">
    <div class="row row--between">
      <h2 id="sub-title">{{ t('sub.title') }}</h2>
      <span class="chip chip--lg num">{{ t('sub.summary', { n: submitted, total: rows.length }) }}</span>
    </div>
    <p class="small muted">{{ t('sub.lead') }}</p>
    <div class="table-scroll">
      <table class="table">
        <thead>
          <tr>
            <th scope="col">{{ t('staff.name') }}</th>
            <th scope="col">{{ t('staff.type') }}</th>
            <th scope="col" class="num">{{ t('sub.available') }}</th>
            <th scope="col" class="num">{{ t('sub.off') }}</th>
            <th scope="col" class="num">{{ t('sub.paid') }}</th>
            <th scope="col" class="num">{{ t('sub.half') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.employee_id">
            <th scope="row">
              <span class="name">
                {{ r.name }}
                <span v-if="r.items.length === 0" class="chip chip--warning">{{ t('sub.notSubmitted') }}</span>
              </span>
            </th>
            <td><span class="chip" :class="{ 'chip--solid': r.is_regular }">{{ r.is_regular ? t('staff.regular') : t('staff.partShort') }}</span></td>
            <td class="num">{{ r.available }}</td>
            <td class="num">{{ r.off }}</td>
            <td class="num">{{ r.paid }}</td>
            <td class="num">{{ r.half }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.name {
  font-weight: 600;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2);
}
</style>
