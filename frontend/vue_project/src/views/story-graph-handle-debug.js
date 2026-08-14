import React, { useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import ReactFlow, {
  Background,
  BaseEdge,
  Handle,
  MarkerType,
  Position,
  getBezierPath,
} from 'reactflow'

import 'reactflow/dist/style.css'

const h = React.createElement

const PROBE_GAP = 220
const PROBE_WIDTH = 104
const PROBE_HEIGHT = 44

export function createHandleDebugRenderer(container) {
  const root = createRoot(container)

  return {
    render(props) {
      root.render(h(HandleDebugCanvas, props))
    },
    destroy() {
      root.unmount()
    },
  }
}

function HandleDebugCanvas({ config }) {
  const [instance, setInstance] = useState(null)
  const didFitRef = useRef(false)

  const model = useMemo(() => buildDebugModel(config), [config])

  useEffect(() => {
    didFitRef.current = false
  }, [model.signature])

  useEffect(() => {
    if (!instance || didFitRef.current) return
    const timer = window.setTimeout(() => {
      const nodes = instance.getNodes?.() || []
      if (!nodes.length) return
      instance.fitView?.({
        nodes,
        padding: 0.28,
        duration: 0,
        includeHiddenNodes: false,
      })
      didFitRef.current = true
    }, 60)
    return () => window.clearTimeout(timer)
  }, [instance, model.signature])

  return h(
    'div',
    {
      style: {
        width: '100%',
        height: '100%',
        background: '#ffffff',
      },
    },
    h(ReactFlow, {
      nodes: model.nodes,
      edges: model.edges,
      nodeTypes: nodeTypes,
      edgeTypes: edgeTypes,
      minZoom: 0.3,
      maxZoom: 2,
      nodesDraggable: true,
      nodesConnectable: false,
      elementsSelectable: true,
      panOnDrag: [2],
      zoomOnScroll: true,
      zoomOnDoubleClick: false,
      selectionOnDrag: false,
      proOptions: { hideAttribution: true },
      onInit: setInstance,
      defaultEdgeOptions: {
        type: 'debugEdge',
      },
      children: [
        h(Background, {
          key: 'bg',
          gap: 20,
          size: 1,
          color: 'rgba(148, 163, 184, 0.15)',
          variant: 'dots',
        }),
      ],
    }),
  )
}

const nodeTypes = {
  centerNode: CenterNode,
  probeNode: ProbeNode,
}

const edgeTypes = {
  debugEdge: DebugEdge,
}

function CenterNode({ data }) {
  const shellStyle = {
    width: `${data.width}px`,
    height: `${data.height}px`,
    borderRadius: `${data.radius}px`,
    border: '1.5px solid #c9d9ee',
    background: '#ffffff',
    boxShadow: '0 14px 30px rgba(148, 163, 184, 0.18)',
    display: 'flex',
    flexDirection: 'column',
    justifyContent: 'center',
    alignItems: 'center',
    gap: '8px',
    color: '#1f334d',
    textAlign: 'center',
  }

  return h(
    'div',
    {
      style: {
        width: `${data.width}px`,
        height: `${data.height}px`,
        position: 'relative',
      },
    },
    [
      renderHandle('target', 'left', Position.Left, data, false),
      renderHandle('target', 'right', Position.Right, data, false),
      renderHandle('target', 'top', Position.Top, data, false),
      renderHandle('target', 'bottom', Position.Bottom, data, false),
      renderHandle('source', 'left', Position.Left, data, true),
      renderHandle('source', 'right', Position.Right, data, true),
      renderHandle('source', 'top', Position.Top, data, true),
      renderHandle('source', 'bottom', Position.Bottom, data, true),
      h('div', { style: shellStyle }, [
        h('strong', { style: { fontSize: '15px' } }, 'Handle Debug Node'),
        h(
          'div',
          { style: { fontSize: '12px', color: '#60758f', lineHeight: 1.45 } },
          `size ${data.handleSize}px · offset ${data.handleOffset}px · arrow ${data.arrowSize}px`,
        ),
      ]),
    ],
  )
}

function ProbeNode({ data }) {
  const sideToPosition = {
    left: Position.Right,
    right: Position.Left,
    top: Position.Bottom,
    bottom: Position.Top,
  }
  const position = sideToPosition[data.side]

  return h(
    'div',
    {
      style: {
        width: `${PROBE_WIDTH}px`,
        height: `${PROBE_HEIGHT}px`,
        borderRadius: '14px',
        border: '1px dashed #c7d5e6',
        background: '#f8fbff',
        color: '#5f738d',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontSize: '12px',
        position: 'relative',
      },
    },
    [
      h(Handle, {
        id: `target-${data.side}`,
        type: 'target',
        position,
        isConnectable: false,
        style: {
          width: 6,
          height: 6,
          background: '#94a3b8',
          border: '0',
          ...positionStyle(position, 6, 0),
        },
      }),
      data.label,
    ],
  )
}

function renderHandle(type, side, position, data, visible) {
  const style = visible
    ? {
        width: data.handleSize,
        height: data.handleSize,
        border: `${data.handleBorderWidth}px solid ${data.handleColor}`,
        background: '#ffffff',
        boxShadow: '0 0 0 1px rgba(255, 255, 255, 0.98)',
        borderRadius: 999,
        opacity: 1,
        ...positionStyle(position, data.handleSize, data.handleOffset),
      }
    : {
        width: data.handleSize,
        height: data.handleSize,
        border: '0 solid transparent',
        background: 'transparent',
        boxShadow: 'none',
        borderRadius: 999,
        opacity: 0,
        ...positionStyle(position, data.handleSize, data.handleOffset),
      }

  return h(Handle, {
    id: `${type}-${side}`,
    type,
    position,
    isConnectable: false,
    style,
  })
}

function positionStyle(position, size, extraOffset) {
  const base = -size / 2 + extraOffset
  if (position === Position.Left) return { left: `${base}px` }
  if (position === Position.Right) return { right: `${base}px` }
  if (position === Position.Top) return { top: `${base}px` }
  return { bottom: `${base}px` }
}

function DebugEdge(props) {
  const {
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    markerEnd,
    data,
  } = props

  const [path] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    curvature: 0.16,
  })

  return h(BaseEdge, {
    id,
    path,
    markerEnd,
    style: {
      stroke: data.stroke,
      strokeWidth: data.strokeWidth,
      strokeDasharray: data.dashArray,
      opacity: 1,
    },
  })
}

function buildDebugModel(config) {
  const width = Number(config.nodeWidth || 172)
  const height = Number(config.nodeHeight || 116)
  const radius = Number(config.nodeRadius || 24)
  const handleSize = Number(config.handleSize || 6)
  const handleOffset = Number(config.handleOffset || 0)
  const handleBorderWidth = Number(config.handleBorderWidth || 1.25)
  const arrowSize = Number(config.arrowSize || 10)
  const strokeWidth = Number(config.edgeWidth || 2.8)

  const centerNode = {
    id: 'center',
    type: 'centerNode',
    position: { x: 0, y: 0 },
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    data: {
      width,
      height,
      radius,
      handleSize,
      handleOffset,
      handleBorderWidth,
      handleColor: '#2563eb',
      arrowSize,
    },
    style: {
      width,
      height,
    },
  }

  const probes = [
    { id: 'probe-left', side: 'left', label: 'Left Probe', x: -PROBE_GAP, y: height / 2 - PROBE_HEIGHT / 2 },
    { id: 'probe-right', side: 'right', label: 'Right Probe', x: width + PROBE_GAP - PROBE_WIDTH, y: height / 2 - PROBE_HEIGHT / 2 },
    { id: 'probe-top', side: 'top', label: 'Top Probe', x: width / 2 - PROBE_WIDTH / 2, y: -PROBE_GAP },
    { id: 'probe-bottom', side: 'bottom', label: 'Bottom Probe', x: width / 2 - PROBE_WIDTH / 2, y: height + PROBE_GAP - PROBE_HEIGHT },
  ].map((probe) => ({
    id: probe.id,
    type: 'probeNode',
    position: { x: probe.x, y: probe.y },
    draggable: false,
    selectable: false,
    data: {
      side: probe.side,
      label: probe.label,
    },
    style: {
      width: PROBE_WIDTH,
      height: PROBE_HEIGHT,
    },
  }))

  const edgeBase = {
    type: 'debugEdge',
    markerEnd: {
      type: MarkerType.ArrowClosed,
      width: arrowSize,
      height: arrowSize,
      color: '#2563eb',
    },
    data: {
      stroke: '#2563eb',
      strokeWidth,
      dashArray: '10 8',
    },
  }

  const edges = [
    { id: 'edge-left', sourceHandle: 'source-left', target: 'probe-left', targetHandle: 'target-left' },
    { id: 'edge-right', sourceHandle: 'source-right', target: 'probe-right', targetHandle: 'target-right' },
    { id: 'edge-top', sourceHandle: 'source-top', target: 'probe-top', targetHandle: 'target-top' },
    { id: 'edge-bottom', sourceHandle: 'source-bottom', target: 'probe-bottom', targetHandle: 'target-bottom' },
  ].map((edge) => ({
    ...edgeBase,
    ...edge,
    source: 'center',
  }))

  return {
    nodes: [centerNode, ...probes],
    edges,
    signature: [width, height, radius, handleSize, handleOffset, handleBorderWidth, arrowSize, strokeWidth].join(':'),
  }
}
