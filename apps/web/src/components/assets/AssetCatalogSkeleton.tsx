export function AssetCatalogSkeleton({ count }: { count: number }) {
  return <>{Array.from({ length: Math.min(96, Math.max(1, count)) }, (_, index) =>
    <div className="r5-skeleton-card" aria-hidden="true" key={index}>
      <div className="r5-skeleton-preview" />
      <div className="r5-skeleton-copy"><i /><i /><i /><i /></div>
    </div>,
  )}</>;
}
