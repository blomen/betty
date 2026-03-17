interface LevelRef {
  name: string;
  price: number;
}

interface Props {
  above: LevelRef[];
  below: LevelRef[];
}

export function NearbyLevelStrip({ above, below }: Props) {
  return (
    <div className="flex-shrink-0 text-[10px] font-mono text-zinc-500 bg-zinc-900/50 border-y border-zinc-800 px-3 py-1 flex items-center gap-1 overflow-hidden">
      <span className="text-zinc-600">↑</span>
      {above.map((l, i) => (
        <span key={l.name}>
          {i > 0 && <span className="text-zinc-700 mx-1">·</span>}
          <span className="text-zinc-500">{l.name}</span>{' '}
          <span className="text-zinc-400">{l.price.toFixed(0)}</span>
        </span>
      ))}
      <span className="text-zinc-700 mx-2">|</span>
      <span className="text-zinc-600">↓</span>
      {below.map((l, i) => (
        <span key={l.name}>
          {i > 0 && <span className="text-zinc-700 mx-1">·</span>}
          <span className="text-zinc-500">{l.name}</span>{' '}
          <span className="text-zinc-400">{l.price.toFixed(0)}</span>
        </span>
      ))}
    </div>
  );
}
