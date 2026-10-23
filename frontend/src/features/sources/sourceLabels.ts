import { SourceName } from "../../types/api";

/** Display names for the anonymised sources, in the order the UI lists them. */
export const SOURCE_LABELS: Record<SourceName, string> = {
  source_a: "Kaynak A",
  source_b: "Kaynak B",
  source_c: "Kaynak C",
  source_d: "Kaynak D",
  source_e: "Kaynak E",
  source_f: "Kaynak F",
};

export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source as SourceName] ?? source;
}
