<script setup lang="ts">
// 30분 단위 시각 선택칸 (V9_ko 165 「30분 단위는 화면이 제시」). label 은 항상 붙는다.
const props = defineProps<{
  id: string
  label: string
  options: readonly string[]
  modelValue: string | null
  invalid?: boolean
  hideLabel?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

function onChange(e: Event) {
  emit('update:modelValue', (e.target as HTMLSelectElement).value)
}
</script>

<template>
  <div class="field">
    <label :for="props.id" :class="{ 'sr-only': props.hideLabel }">{{ props.label }}</label>
    <select :id="props.id" class="select num" :value="props.modelValue ?? ''" :aria-invalid="props.invalid ? 'true' : undefined" @change="onChange">
      <option v-if="props.modelValue === null" value="" disabled>--:--</option>
      <option v-if="props.modelValue !== null && !props.options.includes(props.modelValue)" :value="props.modelValue">{{ props.modelValue }}</option>
      <option v-for="o in props.options" :key="o" :value="o">{{ o }}</option>
    </select>
  </div>
</template>
