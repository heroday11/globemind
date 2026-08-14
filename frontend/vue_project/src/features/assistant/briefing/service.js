import { normalizeBriefingScheduleList } from './model.js'

function responseError(fallback) {
  return new Error(fallback)
}

function requireSuccess(response, fallback) {
  if (!response?.ok) throw responseError(fallback)
  return response
}

async function invokeSafely(operation, fallback) {
  try {
    return await operation()
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw responseError(fallback)
  }
}

export function createBriefingScheduleService(api) {
  if (!api || typeof api !== 'object') {
    throw new TypeError('Briefing schedule service requires an API facade')
  }
  return Object.freeze({
    async list() {
      const response = await invokeSafely(
        async () => requireSuccess(await api.listSchedules(), '读取定时任务失败'),
        '读取定时任务失败',
      )
      return normalizeBriefingScheduleList(response.data)
    },
    async save(id, payload) {
      const response = await invokeSafely(
        async () => requireSuccess(await api.saveSchedule(id, payload), '保存定时任务失败'),
        '保存定时任务失败',
      )
      return response.data
    },
    async run(id) {
      const response = await invokeSafely(
        async () => requireSuccess(await api.runSchedule(id), '运行定时任务失败'),
        '运行定时任务失败',
      )
      return response.data
    },
    async remove(id) {
      const response = await invokeSafely(
        async () => requireSuccess(await api.deleteSchedule(id), '删除定时任务失败'),
        '删除定时任务失败',
      )
      return response
    },
  })
}

export function createLatestBriefingScheduleLoader(service) {
  if (!service || typeof service.list !== 'function') {
    throw new TypeError('Briefing schedule loader requires a list service')
  }
  let generation = 0
  return Object.freeze({
    async load() {
      generation += 1
      const requestGeneration = generation
      try {
        const rows = await service.list()
        if (requestGeneration !== generation) {
          return { current: false, rows: null, error: null }
        }
        return { current: true, rows, error: null }
      } catch (error) {
        if (requestGeneration !== generation) {
          return { current: false, rows: null, error: null }
        }
        return { current: true, rows: null, error }
      }
    },
    invalidate() {
      generation += 1
    },
  })
}
