<script setup>
import { dismissToast, state } from '../stores/store'
</script>

<template>
  <div class="toast-stack" aria-live="polite">
    <div
      v-for="toast in state.toasts"
      :key="toast.id"
      class="toast"
      :class="`toast--${toast.type}`"
      @click="dismissToast(toast.id)"
    >
      <span class="toast-dot"></span>
      <span class="toast-text">{{ toast.text }}</span>
    </div>
  </div>
</template>
