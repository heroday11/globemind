export function createLatestRequestGate() {
  let generation = 0

  return Object.freeze({
    begin() {
      generation += 1
      const requestGeneration = generation
      return () => requestGeneration === generation
    },
    invalidate() {
      generation += 1
    },
  })
}
