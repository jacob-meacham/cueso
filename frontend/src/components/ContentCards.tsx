import { SERVICE_COLORS, SERVICE_DISPLAY_NAMES } from "../constants";
import type { ContentMatch } from "../types";

type Props = {
  matches: ContentMatch[];
  onLaunch: (match: ContentMatch) => void;
  launching: string | null; // content_id currently launching, or null
};

/** Clean up titles like "The Bear | Hulu" → "The Bear" */
function cleanTitle(title: string): string {
  const idx = title.lastIndexOf("|");
  return idx > 0 ? title.slice(0, idx).trim() : title;
}

export default function ContentCards({ matches, onLaunch, launching }: Props) {
  return (
    <div className="scrollbar-hide mt-3 flex gap-3 overflow-x-auto pb-2 snap-x snap-mandatory">
      {matches.map((match) => {
        const color = SERVICE_COLORS[match.service_name] ?? "#6366f1";
        const displayName =
          SERVICE_DISPLAY_NAMES[match.service_name] ?? match.service_name;
        const isLaunching = launching === match.content_id;

        return (
          <div
            key={`${match.service_name}-${match.content_id}`}
            className="min-w-[200px] max-w-[240px] shrink-0 snap-start rounded-xl border border-white/10 bg-[#14141f] p-4"
          >
            {/* Service badge */}
            <div
              className="mb-2 inline-block rounded-md px-2 py-0.5 text-xs font-semibold text-white"
              style={{ backgroundColor: color }}
            >
              {displayName}
            </div>

            {/* Title */}
            <p className="mb-1 text-sm font-medium text-slate-200 leading-tight">
              {cleanTitle(match.title)}
            </p>

            {/* Media type */}
            <p className="mb-3 text-xs text-slate-500 capitalize">
              {match.media_type}
            </p>

            {/* Play button */}
            <button
              onClick={() => onLaunch(match)}
              disabled={isLaunching}
              className="w-full cursor-pointer rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:cursor-wait disabled:opacity-50"
            >
              {isLaunching ? "Launching..." : "Play"}
            </button>
          </div>
        );
      })}
    </div>
  );
}
