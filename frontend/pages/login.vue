<script setup lang="ts">
definePageMeta({ layout: false })

const route = useRoute()
const username = ref("admin")
const password = ref("")
const error = ref("")
const loading = ref(false)

async function submit() {
  error.value = ""
  loading.value = true
  try {
    await apiFetch("/api/auth/login", {
      method: "POST",
      body: { username: username.value.trim(), password: password.value },
    })
    const redirect = typeof route.query.redirect === "string" ? route.query.redirect : "/"
    await navigateTo(redirect || "/")
  } catch (e: unknown) {
    const err = e as { data?: { detail?: string }; statusMessage?: string }
    error.value =
      err?.data?.detail || err?.statusMessage || "فشل تسجيل الدخول — تحقق من اسم المستخدم وكلمة المرور"
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    const status = await apiFetch<{ authRequired: boolean; authenticated: boolean }>(
      "/api/auth/status",
    )
    if (!status.authRequired || status.authenticated) {
      await navigateTo("/")
    }
  } catch {
    /* API offline — show login form */
  }
})
</script>

<template>
  <div class="login-page">
    <form class="login-card panel" @submit.prevent="submit">
      <h1 class="login-title">AlKarrar Pro</h1>
      <p class="login-sub muted">تسجيل الدخول إلى لوحة التداول</p>

      <label class="field-label" for="user">اسم المستخدم</label>
      <input id="user" v-model="username" class="field" type="text" autocomplete="username" />

      <label class="field-label" for="pass">كلمة المرور</label>
      <input
        id="pass"
        v-model="password"
        class="field"
        type="password"
        autocomplete="current-password"
        required
      />

      <p v-if="error" class="login-err" role="alert">{{ error }}</p>

      <button class="btn-primary" type="submit" :disabled="loading">
        {{ loading ? "جاري الدخول…" : "دخول" }}
      </button>

      <p class="login-hint muted">
        عيّن <code>ALKARRAR_DASHBOARD_PASSWORD</code> في ملف <code>.env</code> على السيرفر.
        بدونها تبقى اللوحة مفتوحة محلياً للتطوير.
      </p>
    </form>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.5rem;
}
.login-card {
  width: 100%;
  max-width: 380px;
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
}
.login-title {
  margin: 0;
  font-size: 1.35rem;
}
.login-sub {
  margin: 0 0 0.5rem;
  font-size: 0.85rem;
}
.field-label {
  font-size: 0.75rem;
  color: var(--muted);
}
.login-err {
  margin: 0;
  font-size: 0.8rem;
  color: var(--danger);
}
.btn-primary {
  margin-top: 0.35rem;
  padding: 0.65rem 1rem;
  border: none;
  border-radius: 8px;
  background: var(--accent);
  color: #0b0e11;
  font-weight: 600;
  cursor: pointer;
}
.btn-primary:disabled {
  opacity: 0.6;
  cursor: wait;
}
.login-hint {
  margin: 0.5rem 0 0;
  font-size: 0.7rem;
  line-height: 1.4;
}
.login-hint code {
  font-size: 0.68rem;
}
</style>
