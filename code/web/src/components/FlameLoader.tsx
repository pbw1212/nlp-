/**
 * 「token 燃烧中」加载指示器（线条 emoji 动效）。
 *
 * 素材来源：OpenMoji 的 black 线稿版 🔥（emoji U+1F525）
 *   https://openmoji.org/library/emoji-1F525/
 *   许可：CC BY-SA 4.0（署名-相同方式共享）。本项目做了：改写为 currentColor、
 *   按语义切分为三组（底部弧 / 主焰 / 顶端火苗）并各自加关键帧，仍以同一许可分发。
 *
 * 为什么不用自绘简笔画：线稿 emoji 的造型与比例经过专业设计，缩到十几像素仍然清晰；
 * 我们只负责"切分 + 动"，不再自己画。
 *
 * 尺寸约定：默认 17px —— 比正文汉字（约 14px）略大一点，符合"跟文字做邻居"的体量。
 */

const FLAME_VIEWBOX = '0 0 72 72'

/** 底部弧线（燃烧的底座/余光） */
const BASE_PATH =
  'M21.6298,61.5562c3.479,3.6108,8.6702,5.4754,13.9925,5.4754c5.0546,0,10.7077-1.9624,14.2409-5.4677'

/** 主焰轮廓 */
const BODY_PATH =
  'M21.5108,57.4557c0,0-10.5321-11.2011-0.4546-25.9791c0,0,0.1969,4.0589,1.2551,6.5816c0.4834,1.0362,1.2122,1.9569,2.3487,1.9569 c1.3355,0,2.4181-0.8972,2.4181-2.5708c0-0.2599,0-0.6721,0-0.9184c0.105-3.0996-0.5251-7.6659,3.5708-17.19 c0,0,7.0365,3.7835,3.9909-14.6122c0,0,14.8798,10.4421,14.2762,28.217c0,1.2693,0.9678,2.2983,2.1617,2.2983 s2.1617-1.029,2.1617-2.2983c0.075,0.1341,6.3219,13.078-2.514,24.515'

/** 左上小火苗（燃烧的碎焰） */
const TIP_PATH =
  'M22.8203,22.9729c0.198-1.0358,0.7082-2.2802,1.9228-3.3185c0,0,1.9021-1.6355,1.5953-3.8305'

export function TokenFlame({ size = 17, title = 'token 燃烧中' }: { size?: number; title?: string }) {
  return (
    <span
      className="flame"
      style={{ width: size, height: size }}
      role="img"
      aria-label={title}
      title={title}
    >
      <svg viewBox={FLAME_VIEWBOX} width={size} height={size} aria-hidden="true">
        <g
          fill="none"
          stroke="currentColor"
          strokeWidth="5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path className="flame__base" d={BASE_PATH} />
          <path className="flame__tip" d={TIP_PATH} />
          <path className="flame__body" d={BODY_PATH} />
        </g>
        {/* 飞溅的火星：让"燃烧"有方向感和随机感 */}
        <g stroke="none" fill="currentColor">
          <circle className="flame__spark flame__spark--a" cx="36" cy="9" r="1.7" />
          <circle className="flame__spark flame__spark--b" cx="46" cy="16" r="1.3" />
          <circle className="flame__spark flame__spark--c" cx="30" cy="14" r="1.1" />
        </g>
      </svg>
    </span>
  )
}

/** 生成中的一行：小火苗 + 「token 燃烧中...」，放在回答将要出现的位置 */
export function GeneratingIndicator({
  size = 17,
  text = 'token 燃烧中',
}: {
  size?: number
  text?: string
}) {
  return (
    <div className="generating">
      <TokenFlame size={size} title={text} />
      <span className="generating__text">
        {text}
        <span className="dots" aria-hidden="true">
          <i>.</i>
          <i>.</i>
          <i>.</i>
        </span>
      </span>
    </div>
  )
}
