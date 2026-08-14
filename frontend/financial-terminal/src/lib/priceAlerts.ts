import { randomId } from './randomId'

export type PriceAlertLine = {
  id: string
  /** 监控品种（展示用） */
  instrument: string
  label: string
  price: number
  createdAt: number
}

export function createPriceAlertLine(
  price: number,
  instrument: string,
  index: number,
): PriceAlertLine {
  return {
    id: randomId(),
    instrument,
    label: `风险预警阈值 ${index}`,
    price,
    createdAt: Date.now(),
  }
}
