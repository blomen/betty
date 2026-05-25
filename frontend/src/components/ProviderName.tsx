import { formatProviderName, getProviderPlatform } from '@/utils/formatters';

/** Display provider name with small platform tag: Unibet <span>(kambi)</span> */
export function ProviderName({ name, className }: { name: string; className?: string }) {
  const clean = formatProviderName(name);
  const platform = getProviderPlatform(name);
  return (
    <span className={className}>
      {clean}
      {platform && <span className="text-muted2 text-[9px] ml-0.5">({platform})</span>}
    </span>
  );
}
