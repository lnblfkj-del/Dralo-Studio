/** Peak envelope from decoded samples. Silence remains silence; no decorative data. */
export function audioPeaks(channels: Float32Array[], bars = 80): number[] {
  const length = channels[0]?.length ?? 0;
  return Array.from({length: bars}, (_, i) => {
    let peak = 0;
    for (const channel of channels) for (let j = Math.floor(i * length / bars); j < Math.floor((i + 1) * length / bars); j++) peak = Math.max(peak, Math.abs(channel[j] ?? 0));
    return peak;
  });
}
