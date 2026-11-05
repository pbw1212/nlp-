/**
 * 图标：只用手写的少量 24×24 描边图标，不引第三方图标库。
 * 为什么：图标库（如 lucide）会多一个依赖和几百 KB 体积，而本层只需要 10 个左右的图标。
 */
export type IconName =
  | 'chat'
  | 'memory'
  | 'retrieval'
  | 'tasks'
  | 'collaboration'
  | 'governance'
  | 'context'
  | 'sun'
  | 'moon'
  | 'refresh'
  | 'link'
  | 'key'
  | 'download'
  | 'plus'
  | 'sunrise'
  | 'sunset'
  | 'clock'

const PATHS: Record<IconName, string> = {
  chat: 'M4 5.5h16v10.5H8.5L4 20V5.5z M8 9h8 M8 12.5h5',
  memory: 'M12 3.5 3.5 8 12 12.5 20.5 8 12 3.5z M3.5 13.5 12 18l8.5-4.5',
  retrieval: 'M11 4.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13z M16 16l4 4',
  tasks: 'M4.5 6.5 6 8l2.5-2.5 M4.5 13 6 14.5 8.5 12 M4.5 19.5 6 21l2.5-2.5 M11.5 7h9 M11.5 13.5h9 M11.5 20h9',
  collaboration: 'M12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z M5 12H9.5 M14.5 12H19 M12 9.5V5 M12 14.5V19',
  governance: 'M12 3.5 19 6.5v5.2c0 4.6-3.2 7.4-7 8.3-3.8-.9-7-3.7-7-8.3V6.5l7-3z M9.5 12l1.8 1.8 3.5-3.5',
  context: 'M4 18a8 8 0 1 1 16 0 M12 14l3.5-3.5',
  sun: 'M12 7.5a4.5 4.5 0 1 0 0 9 4.5 4.5 0 0 0 0-9z M12 2.5v2 M12 19.5v2 M2.5 12h2 M19.5 12h2 M5.2 5.2l1.4 1.4 M17.4 17.4l1.4 1.4 M18.8 5.2l-1.4 1.4 M6.6 17.4l-1.4 1.4',
  moon: 'M20 14.5A8.5 8.5 0 1 1 9.5 4a6.8 6.8 0 0 0 10.5 10.5z',
  refresh: 'M20 12a8 8 0 1 1-2.3-5.6 M20 4.5V10h-5.5',
  link: 'M10 13.5a4 4 0 0 0 5.7 0l2.6-2.6a4 4 0 0 0-5.7-5.7L11.4 6.4 M14 10.5a4 4 0 0 0-5.7 0l-2.6 2.6a4 4 0 0 0 5.7 5.7l1.2-1.2',
  key: 'M14.6 9.4a3.6 3.6 0 1 1-3.4-3.4 M11.9 12.2 3.6 20.5h2.5v-2.5h2.5v-2.5h2.2',
  download: 'M12 4v9 M8.5 9.5 12 13l3.5-3.5 M5 15.5v2.5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2.5',
  plus: 'M12 5.5v13 M5.5 12h13',
  sunrise: 'M12 3.5v3.2 M8.4 6.7 12 3.1l3.6 3.6 M3.4 17.4h17.2 M7.2 17.4a4.8 4.8 0 0 1 9.6 0',
  sunset: 'M12 6.7V3.5 M8.4 3.1 12 6.7l3.6-3.6 M3.4 17.4h17.2 M7.2 17.4a4.8 4.8 0 0 1 9.6 0',
  clock: 'M12 20.6a8.6 8.6 0 1 0 0-17.2 8.6 8.6 0 0 0 0 17.2z M12 7.2v5l3.2 2',
}

export function Icon({
  name,
  size = 16,
  className,
}: {
  name: IconName
  size?: number
  className?: string
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      <path d={PATHS[name]} />
    </svg>
  )
}
