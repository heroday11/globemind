import { buildLayoutStoryRelationClaim } from './claims.js'
import {
  normalizeLayoutStoryRelation,
  normalizeStoryRelation,
} from '../../governance/storyRelations.js'

export function buildMacroDisplayEdges(story, visibleEdges) {
  const ordered = story.orderedNodes.slice().sort(compareMacroNodes)
  const laneMap = buildMacroLaneMap(ordered)
  const trunkLane = selectMacroTrunkLane(laneMap)
  const displayEdges = []
  const seen = new Set()

  const addEdge = (candidate) => {
    const edge = candidate?.synthetic === true
      ? normalizeLayoutStoryRelation(candidate)
      : normalizeStoryRelation(candidate)
    if (!edge?.from_id || !edge?.to_id || edge.from_id === edge.to_id) return
    const key = `${edge.from_id}:${edge.to_id}:${edge.edge_type || 'edge'}:${edge.layer || 'story'}`
    if (seen.has(key)) return
    seen.add(key)
    displayEdges.push(edge)
  }

  laneMap.forEach((laneNodes, lane) => {
    laneNodes.forEach((node, index) => {
      if (index === 0) return
      const previous = laneNodes[index - 1]
      const edge = normalizeLayoutStoryRelation({
        from_id: previous.id,
        to_id: node.id,
        source_story_id: story.storyId,
        target_story_id: story.storyId,
        edge_type: lane === trunkLane ? 'macro_sequence' : 'branch_sequence',
        layer: 'story',
        edge_weight: 0.72,
        weight: 0.72,
        relation_reason: `${macroLaneLabel(lane)}推进`,
        evidence_status: 'unavailable',
        synthetic: true,
      })
      edge.claim = buildLayoutStoryRelationClaim({
        graphScopeId: `client-layout:${story.storyId}`,
        fromId: edge.from_id,
        toId: edge.to_id,
        relationKind: edge.edge_type,
      })
      addEdge(edge)
    })
  })

  const trunkNodes = laneMap.get(trunkLane) || ordered
  laneMap.forEach((laneNodes, lane) => {
    if (lane === trunkLane || !laneNodes.length || !trunkNodes.length) return
    const firstNode = laneNodes[0]
    const anchor = findNearestEarlierMacroNode(firstNode, trunkNodes) || trunkNodes[0]
    const branchEdge = normalizeLayoutStoryRelation({
      from_id: anchor.id,
      to_id: firstNode.id,
      source_story_id: story.storyId,
      target_story_id: story.storyId,
      edge_type: 'branch',
      layer: 'story',
      edge_weight: 0.82,
      weight: 0.82,
      relation_reason: `${macroLaneLabel(lane)}分支`,
      evidence_status: 'unavailable',
      synthetic: true,
    })
    branchEdge.claim = buildLayoutStoryRelationClaim({
      graphScopeId: `client-layout:${story.storyId}`,
      fromId: branchEdge.from_id,
      toId: branchEdge.to_id,
      relationKind: branchEdge.edge_type,
    })
    addEdge(branchEdge)

    if (laneNodes.length >= 5) {
      const midNode = laneNodes[Math.floor(laneNodes.length / 2)]
      const midAnchor = findNearestEarlierMacroNode(midNode, trunkNodes)
      if (midAnchor && midAnchor.id !== anchor.id) {
        const contextEdge = normalizeLayoutStoryRelation({
          from_id: midAnchor.id,
          to_id: midNode.id,
          source_story_id: story.storyId,
          target_story_id: story.storyId,
          edge_type: 'context',
          layer: 'story',
          edge_weight: 0.66,
          weight: 0.66,
          relation_reason: `${macroLaneLabel(lane)}布局连接`,
          evidence_status: 'unavailable',
          synthetic: true,
        })
        contextEdge.claim = buildLayoutStoryRelationClaim({
          graphScopeId: `client-layout:${story.storyId}`,
          fromId: contextEdge.from_id,
          toId: contextEdge.to_id,
          relationKind: contextEdge.edge_type,
        })
        addEdge(contextEdge)
      }
    }
  })

  visibleEdges
    .map((edge) => normalizeStoryRelation(edge))
    .filter((edge) => edge.edge_type !== 'macro_sequence')
    .forEach((edge) => addEdge(edge))

  return displayEdges
}

export function buildMacroLaneMap(nodes) {
  const laneMap = new Map()
  nodes.forEach((node) => {
    const lane = normalizeMacroLane(node)
    if (!laneMap.has(lane)) laneMap.set(lane, [])
    laneMap.get(lane).push(node)
  })
  laneMap.forEach((laneNodes) => laneNodes.sort(compareMacroNodes))
  return laneMap
}

export function buildMacroLaneOffsets(laneMap) {
  const trunkLane = selectMacroTrunkLane(laneMap)
  const offsets = new Map([[trunkLane, 0]])
  const laneOrder = ['diplomacy', 'political', 'economic', 'context', 'conflict'].filter(
    (lane) => lane !== trunkLane && laneMap.has(lane),
  )
  const slots = [-220, 220, -400, 400, -560, 560]
  laneOrder.forEach((lane, index) => offsets.set(lane, slots[index] ?? 240 + index * 180))
  return offsets
}

export function buildMacroTimelineX(nodes) {
  const xById = new Map()
  if (!nodes.length) return xById

  const sorted = nodes.slice().sort(compareMacroNodes)
  const points = []
  let cursor = 0
  sorted.forEach((node, index) => {
    if (index > 0) {
      const previous = sorted[index - 1]
      const gapDays = Math.max(0, (macroNodeTime(node) - macroNodeTime(previous)) / 86400000)
      cursor += 176 + clamp(Math.log1p(gapDays) * 16, 0, 48)
    }
    points.push({ id: node.id, x: cursor })
  })

  const center = points.length ? (points[0].x + points[points.length - 1].x) / 2 : 0
  points.forEach((point) => xById.set(point.id, point.x - center))
  return xById
}

export function compareMacroNodes(left, right) {
  const orderDiff = Number(left.node_order || 0) - Number(right.node_order || 0)
  if (orderDiff !== 0) return orderDiff
  const dateDiff = macroNodeTime(left) - macroNodeTime(right)
  if (dateDiff !== 0) return dateDiff
  return String(left.id || '').localeCompare(String(right.id || ''))
}

export function macroNodeTime(node) {
  return Date.parse(node.start_date || node.end_date || node.display_time || '') || 0
}

export function normalizeMacroLane(node) {
  const lane = String(node.lane || node.event_type || '').toLowerCase()
  if (lane.includes('diplomacy') || lane.includes('official')) return 'diplomacy'
  if (lane.includes('economic') || lane.includes('market') || lane.includes('trade')) {
    return 'economic'
  }
  if (lane.includes('political') || lane.includes('policy')) return 'political'
  if (lane.includes('conflict') || lane.includes('military') || lane.includes('security')) {
    return 'conflict'
  }
  return 'context'
}

export function macroLaneLabel(lane) {
  const labels = {
    conflict: '冲突主线',
    diplomacy: '外交支线',
    economic: '经济支线',
    political: '政治支线',
    context: '背景支线',
  }
  return labels[lane] || '角度支线'
}

export function macroLaneXPull(lane) {
  const pulls = {
    diplomacy: -10,
    conflict: 0,
    political: 6,
    economic: 10,
    context: 14,
  }
  return pulls[lane] ?? 0
}

export function avoidMacroCollisions(x, y, placed) {
  const offsets = [0, 62, -62, 124, -124, 186, -186, 248]
  for (const offset of offsets) {
    const candidateY = y + offset
    const collides = placed.some(
      (point) => Math.abs(point.x - x) < 202 && Math.abs(point.y - candidateY) < 118,
    )
    if (!collides) return { x, y: candidateY }
  }
  return { x, y: y + 340 }
}

export function orderStoryNodes(nodes, edges) {
  if (nodes.length <= 1) return nodes.slice()
  if (nodes.some((node) => node.source_kind === 'l3-chain' || node.l2_chain_id)) {
    return nodes.slice().sort((left, right) => {
      const orderDiff = Number(left.node_order || 0) - Number(right.node_order || 0)
      if (orderDiff !== 0) return orderDiff
      return (
        (Date.parse(left.start_date || left.end_date || '') || 0) -
        (Date.parse(right.start_date || right.end_date || '') || 0)
      )
    })
  }

  const nodeMap = new Map(nodes.map((node) => [node.id, node]))
  const outgoing = new Map()
  const incoming = new Set()
  edges.forEach((edge) => {
    outgoing.set(edge.from_id, edge.to_id)
    incoming.add(edge.to_id)
  })

  let cursor = nodes.find((node) => !incoming.has(node.id))?.id || nodes[0].id
  const ordered = []
  const visited = new Set()

  while (cursor && !visited.has(cursor)) {
    visited.add(cursor)
    ordered.push(nodeMap.get(cursor))
    cursor = outgoing.get(cursor)
  }

  const remainder = nodes
    .filter((node) => !visited.has(node.id))
    .sort((left, right) => {
      return (
        (Date.parse(left.display_time || left.start_date || left.end_date || '') || 0) -
        (Date.parse(right.display_time || right.start_date || right.end_date || '') || 0)
      )
    })

  return ordered.concat(remainder).filter(Boolean)
}

function selectMacroTrunkLane(laneMap) {
  if (laneMap.has('conflict')) return 'conflict'
  const priority = ['diplomacy', 'economic', 'political', 'context']
  return Array.from(laneMap.keys()).sort((left, right) => {
    const countDiff = (laneMap.get(right)?.length || 0) - (laneMap.get(left)?.length || 0)
    if (countDiff !== 0) return countDiff
    return priority.indexOf(left) - priority.indexOf(right)
  })[0]
}

function findNearestEarlierMacroNode(node, candidates) {
  const nodeTime = macroNodeTime(node)
  const nodeOrder = Number(node.node_order || 0)
  const sorted = candidates.slice().sort((left, right) => {
    const leftBefore = macroNodeTime(left) <= nodeTime || Number(left.node_order || 0) <= nodeOrder
    const rightBefore =
      macroNodeTime(right) <= nodeTime || Number(right.node_order || 0) <= nodeOrder
    if (leftBefore !== rightBefore) return leftBefore ? -1 : 1
    const leftDistance =
      Math.abs(macroNodeTime(left) - nodeTime) +
      Math.abs(Number(left.node_order || 0) - nodeOrder) * 86400000
    const rightDistance =
      Math.abs(macroNodeTime(right) - nodeTime) +
      Math.abs(Number(right.node_order || 0) - nodeOrder) * 86400000
    return leftDistance - rightDistance
  })
  return sorted[0] || null
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value))
}
