import React, { useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import ReactFlow, {
  Background,
  BaseEdge,
  EdgeLabelRenderer,
  getNodesBounds,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  getBezierPath,
} from 'reactflow'

import 'reactflow/dist/style.css'
import './story-graph-react-flow.css'
import {
  EDGE_COLORS,
  EVENT_PALETTES,
  EVENT_TYPE_LABELS,
  FIT_VIEW_OPTIONS,
  HIDDEN_HANDLE_STYLE,
  INITIAL_FIT_VIEW_OPTIONS,
  STORAGE_KEY_PREFIX,
  VISIBLE_HANDLE_STYLE,
} from './config.js'
import {
  avoidMacroCollisions,
  buildMacroDisplayEdges,
  buildMacroLaneMap,
  buildMacroLaneOffsets,
  buildMacroTimelineX,
  compareMacroNodes,
  macroLaneXPull,
  normalizeMacroLane,
  orderStoryNodes,
} from './flow-model.js'
import {
  buildStoryGraphEdgeInspector,
  buildStoryGraphNodeInspector,
  formatCountLabel,
} from './presentation.js'
import {
  normalizeStoryRelation,
  storyRelationLabel,
} from '../../governance/storyRelations.js'

const h = React.createElement

const nodeTypes = {
  eventNode: StoryEventNode,
  groupNode: StoryGroupNode,
}

const edgeTypes = {
  intelEdge: IntelEdge,
}

export function createStoryGraphRenderer(container) {
  const root = createRoot(container)
  const api = {
    fitView: null,
    autoLayout: null,
    saveLayout: null,
  }

  return {
    render(props) {
      root.render(
        h(StoryGraphCanvas, {
          ...props,
          onReady(nextApi) {
            api.fitView = nextApi.fitView
            api.autoLayout = nextApi.autoLayout
            api.saveLayout = nextApi.saveLayout
          },
        }),
      )
    },
    fitView(options) {
      api.fitView?.(options || FIT_VIEW_OPTIONS)
    },
    autoLayout() {
      api.autoLayout?.()
    },
    saveLayout() {
      api.saveLayout?.()
    },
    destroy() {
      api.fitView = null
      api.autoLayout = null
      api.saveLayout = null
      root.unmount()
    },
  }
}

function StoryGraphCanvas({
  storyGraph,
  showContextLinks,
  onSelectInspector,
  onCanvasClick,
  onReady,
}) {
  const canvasRef = useRef(null)
  const [instance, setInstance] = useState(null)
  const [manualPositions, setManualPositions] = useState({})
  const [nodes, setNodes] = useState([])
  const [saveState, setSaveState] = useState('idle')
  const didAutoFitRef = useRef(false)
  const storageKey = `${STORAGE_KEY_PREFIX}${storyGraph?.story_id || 'unknown'}`

  useEffect(() => {
    setManualPositions(readSavedLayout(storageKey))
    setSaveState('idle')
    didAutoFitRef.current = false
  }, [storageKey])

  const model = useMemo(
    () => buildFlowModel(storyGraph, showContextLinks, manualPositions),
    [storyGraph, showContextLinks, manualPositions],
  )
  const eventNodes = useMemo(() => nodes.filter((node) => node.type === 'eventNode'), [nodes])

  useEffect(() => {
    setNodes(model.nodes)
  }, [model])

  useEffect(() => {
    if (!instance || !eventNodes.length || didAutoFitRef.current) return
    let cancelled = false
    let attempt = 0
    let timer = 0
    let frame = 0

    const scheduleFit = (delay) => {
      timer = window.setTimeout(() => {
        frame = window.requestAnimationFrame(() => {
          if (cancelled || didAutoFitRef.current) return
          const applied = applyViewportToEventNodes(
            instance,
            eventNodes,
            canvasRef.current,
            INITIAL_FIT_VIEW_OPTIONS,
          )
          if (applied) {
            didAutoFitRef.current = true
            return
          }
          attempt += 1
          if (attempt < 8) {
            scheduleFit(80)
          }
        })
      }, delay)
    }

    scheduleFit(90)

    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
      if (frame) window.cancelAnimationFrame(frame)
    }
  }, [eventNodes, instance, model.fitSignature])

  useEffect(() => {
    if (!instance) return
    onReady?.({
      fitView(options) {
        applyViewportToEventNodes(
          instance,
          eventNodes,
          canvasRef.current,
          options || FIT_VIEW_OPTIONS,
        )
      },
      autoLayout() {
        setManualPositions({})
        clearSavedLayout(storageKey)
        setSaveState('idle')
      },
      saveLayout() {
        const nextLayout = collectEventNodePositions(nodes)
        writeSavedLayout(storageKey, nextLayout)
        setManualPositions(nextLayout)
        setSaveState('saved')
      },
    })
  }, [eventNodes, instance, nodes, onReady, storageKey])

  useEffect(() => {
    if (saveState !== 'saved') return
    const timer = window.setTimeout(() => setSaveState('idle'), 1400)
    return () => window.clearTimeout(timer)
  }, [saveState])

  function handleNodeDrag(_event, draggedNode) {
    if (draggedNode.type !== 'eventNode') return
    setNodes((currentNodes) => {
      const movedNodes = currentNodes.map((node) => {
        if (node.id !== draggedNode.id) return node
        return {
          ...node,
          position: {
            x: draggedNode.position.x,
            y: draggedNode.position.y,
          },
        }
      })
      return refreshGroupNodeBounds(movedNodes)
    })
  }

  function handleNodeDragStop(_event, draggedNode) {
    if (draggedNode.type !== 'eventNode') return
    setManualPositions((current) => ({
      ...current,
      [draggedNode.id]: {
        x: draggedNode.position.x,
        y: draggedNode.position.y,
      },
    }))
    setSaveState('dirty')
  }

  function handleSaveLayout() {
    const nextLayout = collectEventNodePositions(nodes)
    writeSavedLayout(storageKey, nextLayout)
    setManualPositions(nextLayout)
    setSaveState('saved')
  }

  function handleAutoLayout() {
    setManualPositions({})
    clearSavedLayout(storageKey)
    setSaveState('idle')
  }

  return h(
    'div',
    { className: 'story-rf-root', ref: canvasRef },
    h(
      ReactFlow,
      {
        nodes,
        edges: model.edges,
        nodeTypes,
        edgeTypes,
        minZoom: 0.08,
        maxZoom: 1.7,
        nodesDraggable: true,
        nodesConnectable: false,
        elementsSelectable: true,
        nodesFocusable: true,
        edgesFocusable: true,
        panOnDrag: [0, 2],
        zoomOnScroll: true,
        selectionOnDrag: false,
        zoomOnDoubleClick: false,
        deleteKeyCode: null,
        defaultEdgeOptions: {
          type: 'intelEdge',
          markerEnd: {
            type: MarkerType.ArrowClosed,
            width: 8,
            height: 8,
            color: '#89a9d5',
          },
        },
        proOptions: { hideAttribution: true },
        onInit: setInstance,
        onPaneContextMenu(event) {
          event.preventDefault()
        },
        onNodeDrag: handleNodeDrag,
        onNodeDragStop: handleNodeDragStop,
        onNodeClick(_event, node) {
          if (node.data?.payload) {
            onSelectInspector?.(node.data.payload)
          }
        },
        onEdgeClick(_event, edge) {
          if (edge.data?.payload) onSelectInspector?.(edge.data.payload)
        },
        onSelectionChange({ nodes: selectedNodes = [], edges: selectedEdges = [] }) {
          const nodePayload = selectedNodes.find((node) => node.data?.payload)?.data?.payload
          if (nodePayload) {
            onSelectInspector?.(nodePayload)
            return
          }
          const edgePayload = selectedEdges.find((edge) => edge.data?.payload)?.data?.payload
          if (edgePayload) onSelectInspector?.(edgePayload)
        },
        onPaneClick() {
          onCanvasClick?.()
        },
      },
      [
        h(Background, {
          key: 'background',
          gap: 30,
          size: 1.1,
          color: 'rgba(168, 193, 226, 0.15)',
          variant: 'dots',
        }),
        h(MiniMap, {
          key: 'minimap',
          position: 'top-left',
          pannable: true,
          zoomable: true,
          className: 'story-rf-minimap',
          nodeStrokeWidth: 0,
          nodeColor(node) {
            return node.data?.miniColor || 'rgba(97, 120, 148, 0.2)'
          },
        }),
        h(
          Panel,
          {
            key: 'toolbar',
            position: 'top-right',
            className: 'story-rf-toolbar',
          },
          [
            h(
              'button',
              {
                key: 'save',
                type: 'button',
                className: ['story-rf-toolbar__button', saveState === 'saved' ? 'is-saved' : '']
                  .join(' ')
                  .trim(),
                onClick: handleSaveLayout,
              },
              saveState === 'saved' ? '已保存' : '保存',
            ),
            h(
              'button',
              {
                key: 'layout',
                type: 'button',
                className: 'story-rf-toolbar__button is-primary',
                onClick: handleAutoLayout,
              },
              '自动布局',
            ),
          ],
        ),
      ],
    ),
  )
}

function StoryEventNode({ data, selected }) {
  return h(
    'div',
    {
      className: ['story-rf-event', `is-${data.storyRole}`, selected ? 'is-selected' : '']
        .join(' ')
        .trim(),
      style: {
        '--event-fill': data.fill,
        '--event-stroke': data.stroke,
        '--event-shadow': data.shadow,
        width: `${data.width}px`,
        height: `${data.height}px`,
      },
    },
    [
      renderHandle('target', 'left', Position.Left, false),
      renderHandle('target', 'right', Position.Right, false),
      renderHandle('target', 'top', Position.Top, false),
      renderHandle('target', 'bottom', Position.Bottom, false),
      renderHandle('source', 'left', Position.Left, false),
      renderHandle('source', 'right', Position.Right, false),
      renderHandle('source', 'top', Position.Top, false),
      renderHandle('source', 'bottom', Position.Bottom, false),
      h('div', { className: 'story-rf-event__shell' }, [
        h(
          'div',
          { className: 'story-rf-event__type' },
          data.sequenceLabel
            ? `${data.sequenceLabel} · ${data.eventTypeLabel}`
            : data.eventTypeLabel,
        ),
        h('div', { className: 'story-rf-event__title' }, data.title),
        data.timeLabel ? h('div', { className: 'story-rf-event__time' }, data.timeLabel) : null,
        h(
          'div',
          { className: 'story-rf-event__meta' },
          data.metaText || formatCountLabel(data.articleCount, {
            unit: '条新闻',
            unknown: '新闻数未知',
          }),
        ),
      ]),
    ],
  )
}

function StoryGroupNode({ data }) {
  return h(
    'div',
    {
      className: ['story-rf-group', `is-${data.storyRole}`].join(' '),
      style: {
        width: `${data.width}px`,
        height: `${data.height}px`,
      },
    },
    [
      h('div', { className: 'story-rf-group__header' }, [
        h(
          'span',
          { className: 'story-rf-group__kicker' },
          data.isMacro
            ? 'L3 大事件'
            : data.storyRole === 'primary'
              ? 'L2 主链'
              : data.relationLayerLabel,
        ),
        h('strong', null, data.title),
        data.subtitle ? h('small', null, data.subtitle) : null,
      ]),
    ],
  )
}

function renderHandle(type, side, position, visible) {
  return h(Handle, {
    id: `${type}-${side}`,
    type,
    position,
    isConnectable: true,
    style: visible ? VISIBLE_HANDLE_STYLE : HIDDEN_HANDLE_STYLE,
  })
}

function IntelEdge(props) {
  const {
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    data,
    markerEnd,
    className,
  } = props

  const pathArgs = {
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  }
  const [path, labelX, labelY] = getBezierPath({
    ...pathArgs,
    curvature: data.curvature ?? (data.layer === 'context' ? 0.36 : 0.24),
  })

  const label = data.label && data.layer !== 'context' ? data.label : null

  return h(React.Fragment, null, [
    h(BaseEdge, {
      key: `${id}-path`,
      id,
      path,
      markerEnd,
      className,
      interactionWidth: 44,
      style: {
        stroke: data.stroke,
        strokeWidth: data.strokeWidth,
        strokeDasharray: data.dashArray,
        opacity: data.opacity,
      },
    }),
    label
      ? h(
          EdgeLabelRenderer,
          { key: `${id}-label` },
          h(
            'div',
            {
              className: 'story-rf-edge-label',
              style: {
                transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              },
            },
            label,
          ),
        )
      : null,
  ])
}

function buildFlowModel(storyGraph, includeContext, positionOverrides = {}) {
  const storyMap = new Map()
  const positions = new Map()
  const eventNodes = []
  const groupNodes = []
  const edges = []
  const visibleEdges = (storyGraph?.edges || [])
    .map((edge) => normalizeStoryRelation(edge))
    .filter((edge) => includeContext || edge.layer !== 'context')

  for (const node of storyGraph?.nodes || []) {
    const storyId = String(node.story_id || storyGraph.story_id)
    if (!storyMap.has(storyId)) {
      storyMap.set(storyId, {
        storyId,
        storyRole: storyId === String(storyGraph.story_id) ? 'primary' : 'branch',
        storyTitle:
          node.story_title ||
          (storyId === String(storyGraph.story_id) ? storyGraph.story_title : `走势链 ${storyId}`),
        relation:
          (storyGraph.related_stories || []).find((item) => String(item.story_id) === storyId) ||
          null,
        nodes: [],
        edges: [],
      })
    }
    storyMap.get(storyId).nodes.push(node)
  }

  for (const edge of visibleEdges) {
    const storyId = String(edge.source_story_id || edge.target_story_id || storyGraph.story_id)
    if (edge.layer === 'story' || String(edge.source_story_id) === String(edge.target_story_id)) {
      storyMap.get(storyId)?.edges.push(edge)
    }
  }

  for (const story of storyMap.values()) {
    story.orderedNodes = orderStoryNodes(story.nodes, story.edges)
  }

  const primaryStoryId = String(storyGraph?.story_id)
  const primaryStory = storyMap.get(primaryStoryId)
  if (!primaryStory) {
    return { nodes: [], edges: [], fitSignature: 0 }
  }

  layoutPrimaryStory(primaryStory, positions)

  const bridgeEdges = visibleEdges.filter(
    (edge) =>
      edge.layer &&
      edge.layer !== 'story' &&
      String(edge.source_story_id) !== String(edge.target_story_id),
  )

  const branches = Array.from(storyMap.values())
    .filter((story) => story.storyId !== primaryStoryId)
    .sort((left, right) => {
      const leftPriority = left.relation?.layer === 'backbone' ? 0 : 1
      const rightPriority = right.relation?.layer === 'backbone' ? 0 : 1
      if (leftPriority !== rightPriority) return leftPriority - rightPriority
      return String(left.storyId).localeCompare(String(right.storyId))
    })

  const occupiedBySide = new Map()
  branches.forEach((story, index) => {
    const anchorX = resolveAnchorX(
      story.storyId,
      bridgeEdges,
      positions,
      primaryStoryId,
      index,
      branches.length,
    )
    const side = index % 2 === 0 ? -1 : 1
    const lane = occupiedBySide.get(side) || 0
    occupiedBySide.set(side, lane + 1)
    layoutBranchStory(story, positions, anchorX, side, lane)
  })

  for (const story of [primaryStory, ...branches]) {
    const { width: nodeWidth, height: nodeHeight } = getNodeSize(story.storyRole)
    story.orderedNodes.forEach((node) => {
      const override = positionOverrides[node.id]
      if (!override) return
      const existing = positions.get(node.id) || {}
      positions.set(node.id, {
        ...existing,
        x: override.x + nodeWidth / 2,
        y: override.y + nodeHeight / 2,
      })
    })
  }

  const groupedStories = [primaryStory, ...branches]
  groupedStories.forEach((story) => {
    const bounds = computeStoryBounds(story, positions)
    groupNodes.push({
      id: `group-${story.storyId}`,
      type: 'groupNode',
      position: { x: bounds.x, y: bounds.y },
      draggable: false,
      selectable: false,
      connectable: false,
      focusable: false,
      data: {
        storyId: story.storyId,
        storyRole: story.storyRole,
        isMacro: isMacroStory(story),
        title: story.storyTitle,
        subtitle: buildStorySubtitle(story),
        relationLayerLabel: story.relation?.layer === 'backbone' ? '主干分支' : '上下文分支',
        width: bounds.width,
        height: bounds.height,
      },
      style: {
        width: bounds.width,
        height: bounds.height,
        zIndex: 0,
      },
    })

    story.orderedNodes.forEach((node, index) => {
      const point = positions.get(node.id)
      const isPrimary = story.storyRole === 'primary'
      const fill = eventColor(node.event_type)
      const stroke = eventBorderColor(node.event_type, isPrimary)
      const shadow = eventShadowColor(node.event_type, isPrimary)
      const { width: nodeWidth, height: nodeHeight } = getNodeSize(story.storyRole)
      const isMacro = isMacroStory(story)
      const inspectorPayload = buildStoryGraphNodeInspector(node, { storyId: story.storyId })

      eventNodes.push({
        id: node.id,
        type: 'eventNode',
        position: {
          x: point.x - nodeWidth / 2,
          y: point.y - nodeHeight / 2,
        },
        sourcePosition: point.defaultSourcePosition,
        targetPosition: point.defaultTargetPosition,
        focusable: true,
        ariaLabel: `图谱节点：${inspectorPayload.title}；${formatNodeTimeRange(node.start_date, node.end_date) || '日期未知'}；${formatCountLabel(node.article_count, { unit: '条新闻', unknown: '新闻数未知' })}`,
        data: {
          payload: inspectorPayload,
          storyId: story.storyId,
          storyRole: story.storyRole,
          sequenceLabel: isMacro ? String(index + 1).padStart(2, '0') : '',
          fill,
          stroke,
          shadow,
          width: nodeWidth,
          height: nodeHeight,
          eventTypeLabel:
            EVENT_TYPE_LABELS[node.event_type] ||
            String(node.event_type || '事件').replaceAll('_', ' '),
          title: shorten(
            node.label || `${node.initiator || '?'} → ${node.target || '?'}`,
            isPrimary ? 38 : 36,
          ),
          articleCount: node.article_count ?? null,
          segmentCount: node.segment_count ?? null,
          metaText:
            node.source_kind === 'l3-chain' || node.l2_chain_id
              ? `${formatCountLabel(node.segment_count, { unit: '个片段', unknown: '片段数未知' })} · ${formatCountLabel(node.article_count, { unit: '条新闻', unknown: '新闻数未知' })}`
              : '',
          timeLabel: formatNodeTimeRange(node.start_date, node.end_date),
          miniColor: fill,
        },
        style: {
          zIndex: 6,
        },
      })
    })
  })

  const flowEdges = isMacroStory(primaryStory)
    ? buildMacroDisplayEdges(primaryStory, visibleEdges)
    : visibleEdges

  flowEdges.forEach((edge) => {
    const sourcePoint = positions.get(edge.from_id)
    const targetPoint = positions.get(edge.to_id)
    if (!sourcePoint || !targetPoint) return

    const handles = chooseHandles(sourcePoint, targetPoint)
    const isContext = edge.layer === 'context'
    const isMacroSequence =
      (sourcePoint.isMacro || targetPoint.isMacro) &&
      (edge.edge_type === 'macro_sequence' || edge.edge_type === 'branch_sequence')
    const isMacroStoryEdge = sourcePoint.isMacro || targetPoint.isMacro
    const stroke = edgeStroke(edge)
    const inspectorPayload = buildStoryGraphEdgeInspector(edge)

    edges.push({
      id: `edge-${edge.from_id}-${edge.to_id}-${edge.edge_type}-${edge.layer || 'story'}`,
      source: edge.from_id,
      target: edge.to_id,
      ariaLabel: `图谱关系：${inspectorPayload.title}；${inspectorPayload.subtitle}`,
      sourceHandle: `source-${handles.source}`,
      targetHandle: `target-${handles.target}`,
      type: 'intelEdge',
      className: [
        'story-rf-edge',
        isContext ? 'story-rf-edge--context' : 'story-rf-edge--story',
        isMacroStoryEdge ? 'story-rf-edge--macro' : '',
        isMacroSequence ? 'story-rf-edge--macro-sequence' : '',
      ]
        .filter(Boolean)
        .join(' '),
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 11,
        height: 11,
        color: stroke,
      },
      zIndex: 18,
      data: {
        payload: inspectorPayload,
        layer: edge.layer || 'story',
        route: 'bezier',
        edgeType: edge.edge_type,
        relationReason: edge.relation_reason,
        weight: null,
        stroke,
        strokeWidth: isContext ? 2.4 : isMacroSequence ? 2.8 : edge.layer === 'story' ? 3.1 : 2.8,
        dashArray: isContext
          ? '7 10'
          : isMacroSequence
            ? ''
            : edge.layer === 'story'
              ? '11 8'
              : '9 8',
        opacity: isContext ? 0.78 : isMacroSequence ? 0.76 : 0.94,
        curvature: isContext ? 0.34 : isMacroStoryEdge ? (isMacroSequence ? 0.22 : 0.3) : 0.22,
        label: shouldShowEdgeLabel(edge, isMacroStoryEdge) ? edgeLabel(edge) : '',
      },
    })
  })

  return {
    nodes: [...groupNodes, ...eventNodes],
    edges,
    fitSignature: eventNodes.length * 1000 + edges.length + Object.keys(positionOverrides).length,
  }
}

function getNodeSize(storyRole) {
  return storyRole === 'primary' ? { width: 210, height: 116 } : { width: 210, height: 116 }
}

function layoutPrimaryStory(story, positions) {
  if (isMacroStory(story)) {
    layoutMacroStory(story, positions)
    return
  }
  const count = story.orderedNodes.length
  if (!count) return

  const gap = 236
  const startX = -((count - 1) * gap) / 2

  story.orderedNodes.forEach((node, index) => {
    positions.set(node.id, {
      x: startX + index * gap,
      y: 0,
      defaultSourcePosition: Position.Right,
      defaultTargetPosition: Position.Left,
    })
  })
}

function isMacroStory(story) {
  return story.nodes?.some((node) => node.source_kind === 'l3-chain' || node.l2_chain_id)
}

function layoutMacroStory(story, positions) {
  const ordered = story.orderedNodes.slice().sort(compareMacroNodes)
  const count = ordered.length
  if (!count) return

  const laneMap = buildMacroLaneMap(ordered)
  const laneOffsets = buildMacroLaneOffsets(laneMap)
  const xById = buildMacroTimelineX(ordered)
  const placed = []

  ordered.forEach((node, index) => {
    const lane = normalizeMacroLane(node)
    const laneNodes = laneMap.get(lane) || []
    const laneIndex = Math.max(
      0,
      laneNodes.findIndex((item) => item.id === node.id),
    )
    const laneWave = Math.sin(laneIndex * 0.9 + index * 0.13) * 20
    const timeWave = Math.cos(index * 0.47) * 12
    let x = (xById.get(node.id) || 0) + macroLaneXPull(lane)
    let y = (laneOffsets.get(lane) || 0) + laneWave + timeWave

    const collision = avoidMacroCollisions(x, y, placed)
    x = collision.x
    y = collision.y
    placed.push({ x, y })

    positions.set(node.id, {
      x,
      y,
      isMacro: true,
      timelineIndex: index,
      lane,
      defaultSourcePosition: Position.Right,
      defaultTargetPosition: Position.Left,
    })
  })
}

function layoutBranchStory(story, positions, anchorX, side, lane) {
  const count = story.orderedNodes.length
  if (!count) return

  const gap = 218
  const centerY = side < 0 ? -(260 + lane * 230) : 260 + lane * 230
  const startX = anchorX - ((count - 1) * gap) / 2

  story.orderedNodes.forEach((node, index) => {
    positions.set(node.id, {
      x: startX + index * gap,
      y: centerY,
      defaultSourcePosition: Position.Right,
      defaultTargetPosition: Position.Left,
    })
  })
}

function resolveAnchorX(storyId, bridgeEdges, positions, primaryStoryId, index, total) {
  const connected = bridgeEdges
    .map((edge) => {
      const primaryNodeId =
        String(edge.source_story_id) === primaryStoryId && String(edge.target_story_id) === storyId
          ? edge.from_id
          : String(edge.target_story_id) === primaryStoryId &&
              String(edge.source_story_id) === storyId
            ? edge.to_id
            : null
      return primaryNodeId ? positions.get(primaryNodeId)?.x : null
    })
    .filter((value) => Number.isFinite(value))

  if (!connected.length) {
    return (index - (total - 1) / 2) * 280
  }
  return connected.reduce((sum, value) => sum + value, 0) / connected.length
}

function computeStoryBounds(story, positions) {
  const { width: nodeWidth, height: nodeHeight } = getNodeSize(story.storyRole)
  const points = story.orderedNodes.map((node) => positions.get(node.id)).filter(Boolean)
  const xs = points.map((point) => point.x)
  const ys = points.map((point) => point.y)
  const minX = Math.min(...xs) - nodeWidth / 2 - 38
  const maxX = Math.max(...xs) + nodeWidth / 2 + 38
  const minY = Math.min(...ys) - nodeHeight / 2 - 76
  const maxY = Math.max(...ys) + nodeHeight / 2 + 48
  return {
    x: minX,
    y: minY,
    width: maxX - minX,
    height: maxY - minY,
  }
}

function chooseHandles(sourcePoint, targetPoint) {
  const dx = targetPoint.x - sourcePoint.x
  const dy = targetPoint.y - sourcePoint.y
  if (Math.abs(dx) >= Math.abs(dy)) {
    return dx >= 0 ? { source: 'right', target: 'left' } : { source: 'left', target: 'right' }
  }
  return dy >= 0 ? { source: 'bottom', target: 'top' } : { source: 'top', target: 'bottom' }
}

function buildStorySubtitle(story) {
  const relationLabel = story.relation
    ? storyRelationLabel(story.relation.relation_type)
    : ''
  const range = formatRange(
    story.orderedNodes[0]?.start_date,
    story.orderedNodes[story.orderedNodes.length - 1]?.end_date,
  )
  return [relationLabel, range].filter(Boolean).join(' · ')
}

function formatNodeTimeRange(start, end) {
  if (!start && !end) return ''
  const startText = start ? String(start).slice(0, 10) : ''
  const endText = end ? String(end).slice(0, 10) : ''
  if (!startText) return endText
  if (!endText || startText === endText) return startText
  if (startText.slice(0, 4) === endText.slice(0, 4)) {
    return `${startText} ~ ${endText.slice(5)}`
  }
  return `${startText} ~ ${endText}`
}

function formatRange(start, end) {
  if (!start && !end) return '无日期'
  if (start && end && start !== end)
    return `${String(start).slice(0, 10)} → ${String(end).slice(0, 10)}`
  return start || end
}

function shorten(text, limit) {
  const value = String(text || '').trim()
  if (!value) return '未命名'
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value
}

function edgeLabel(edge) {
  const normalized = storyRelationLabel(edge.edge_type)
  return edge.layer === 'story' ? shorten(normalized, 18) : shorten(normalized, 20)
}

function shouldShowEdgeLabel(edge, isMacroStoryEdge) {
  if (!isMacroStoryEdge) return true
  if (edge.layer === 'context') return false
  return (
    edge.edge_type && edge.edge_type !== 'macro_sequence' && edge.edge_type !== 'branch_sequence'
  )
}

function edgeStroke(edge) {
  if (edge.layer === 'context') return EDGE_COLORS.context
  return EDGE_COLORS[edge.edge_type] || EDGE_COLORS.continuation
}

function eventColor(eventType) {
  return EVENT_PALETTES[eventPaletteKey(eventType)].fill
}

function eventBorderColor(eventType) {
  return EVENT_PALETTES[eventPaletteKey(eventType)].stroke
}

function eventShadowColor(eventType) {
  return EVENT_PALETTES[eventPaletteKey(eventType)].shadow
}

function eventPaletteKey(eventType) {
  const type = String(eventType || '').toLowerCase()
  if (
    [
      'military',
      'terrorism_espionage',
      'protest_repression',
      'conflict',
      'video_clip',
      'main_event',
    ].includes(type)
  ) {
    return 'orange'
  }
  if (['trade_conflict', 'economic', 'market_reaction', 'outcome_reaction'].includes(type)) {
    return 'yellow'
  }
  if (
    [
      'diplomacy',
      'official_update',
      'aid_disaster',
      'human_rights_migration',
      'resolution',
      'de-escalation',
    ].includes(type)
  ) {
    return 'green'
  }
  return 'purple'
}

function readSavedLayout(storageKey) {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(storageKey) || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    return Object.fromEntries(
      Object.entries(parsed).filter(([, point]) => {
        return Number.isFinite(point?.x) && Number.isFinite(point?.y)
      }),
    )
  } catch {
    return {}
  }
}

function writeSavedLayout(storageKey, layout) {
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(layout))
  } catch {
    return
  }
}

function clearSavedLayout(storageKey) {
  try {
    window.localStorage.removeItem(storageKey)
  } catch {
    return
  }
}

function collectEventNodePositions(nodes) {
  return nodes.reduce((acc, node) => {
    if (node.type !== 'eventNode') return acc
    acc[node.id] = {
      x: node.position.x,
      y: node.position.y,
    }
    return acc
  }, {})
}

function refreshGroupNodeBounds(nodes) {
  const groupedBounds = new Map()

  nodes.forEach((node) => {
    if (node.type !== 'eventNode') return
    const storyId = node.data?.storyId
    if (!storyId) return
    const left = node.position.x
    const top = node.position.y
    const right = left + (node.data?.width || 0)
    const bottom = top + (node.data?.height || 0)
    const current = groupedBounds.get(storyId) || {
      minX: left,
      maxX: right,
      minY: top,
      maxY: bottom,
    }
    current.minX = Math.min(current.minX, left)
    current.maxX = Math.max(current.maxX, right)
    current.minY = Math.min(current.minY, top)
    current.maxY = Math.max(current.maxY, bottom)
    groupedBounds.set(storyId, current)
  })

  return nodes.map((node) => {
    if (node.type !== 'groupNode') return node
    const bounds = groupedBounds.get(node.data?.storyId)
    if (!bounds) return node
    const position = {
      x: bounds.minX - 52,
      y: bounds.minY - 92,
    }
    const width = bounds.maxX - bounds.minX + 104
    const height = bounds.maxY - bounds.minY + 144
    return {
      ...node,
      position,
      data: {
        ...node.data,
        width,
        height,
      },
      style: {
        ...node.style,
        width,
        height,
        zIndex: 0,
      },
    }
  })
}

function applyViewportToEventNodes(instance, nodes, container, options = FIT_VIEW_OPTIONS) {
  if (!instance || !nodes?.length || !container || !instance.viewportInitialized) return false

  const targetIds = new Set(nodes.map((node) => node.id))
  const measuredNodes = (instance.getNodes?.() || []).filter(
    (node) =>
      node.type === 'eventNode' &&
      targetIds.has(node.id) &&
      Number.isFinite(node.width) &&
      Number.isFinite(node.height) &&
      node.width > 0 &&
      node.height > 0,
  )
  if (!measuredNodes.length) return false

  const fitNodes = Number.isFinite(options.maxNodes) && options.maxNodes > 0
    ? measuredNodes.slice(0, options.maxNodes)
    : measuredNodes
  const bounds = getNodesBounds(fitNodes)
  if (
    !Number.isFinite(bounds.x) ||
    !Number.isFinite(bounds.y) ||
    bounds.width <= 0 ||
    bounds.height <= 0
  ) {
    return false
  }

  instance.fitView?.({
    nodes: fitNodes,
    padding: typeof options.padding === 'number' ? options.padding : FIT_VIEW_OPTIONS.padding,
    duration: options.duration ?? FIT_VIEW_OPTIONS.duration,
    includeHiddenNodes: false,
  })
  return true
}
