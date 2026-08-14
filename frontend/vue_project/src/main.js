import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import './styles/display-preferences.css'
import { initializeDisplayPreferences } from './composables/useDisplayPreferences.js'

initializeDisplayPreferences()

const app = createApp(App)

app.use(createPinia())
app.use(router)

app.mount('#app')
