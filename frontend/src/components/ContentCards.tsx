import { useState } from "react";
import { SERVICE_COLORS, SERVICE_DISPLAY_NAMES } from "../constants";
import type { ContentMatch } from "../types";

type Props = {
  matches: ContentMatch[];
  onLaunch: (match: ContentMatch) => void;
  launching: string | null; // content_id currently launching, or null
};

/** Clean up titles like "Watch The Bear | Watch Full Episodes | Hulu" → "The Bear" */
function cleanTitle(title: string): string {
  const idx = title.indexOf("|");
  const base = idx > 0 ? title.slice(0, idx).trim() : title;
  return base.replace(/^watch\s+/i, "");
}

/** "S2 E5" / "Season 2" / "Episode 5", or null when the request had neither.
 * media_type is deliberately not shown: it's the Roku deep-link param
 * (defaults to "movie" for most services), not a display fact. */
function seasonEpisode(match: ContentMatch): string | null {
  if (match.season != null && match.episode != null) {
    return `S${match.season} E${match.episode}`;
  }
  if (match.season != null) return `Season ${match.season}`;
  if (match.episode != null) return `Episode ${match.episode}`;
  return null;
}

function Poster({ url, title }: { url: string; title: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return null;
  return (
    <img
      src={url}
      alt={`${title} poster`}
      loading="lazy"
      onError={() => setFailed(true)}
      className="aspect-[2/3] w-full rounded-t-xl object-cover"
    />
  );
}

export default function ContentCards({ matches, onLaunch, launching }: Props) {
  return (
    <div className="scrollbar-hide mt-3 flex gap-3 overflow-x-auto pb-2 snap-x snap-mandatory">
      {matches.map((match) => {
        const color = SERVICE_COLORS[match.service_name] ?? "#6366f1";
        const displayName =
          SERVICE_DISPLAY_NAMES[match.service_name] ?? match.service_name;
        const isLaunching = launching === match.content_id;
        const canResume =
          match.service_name === "emby" && match.resume_position_ticks != null;
        // deep_link=false (Apple TV): launch only opens the app, the user
        // picks the title there — don't promise "Play".
        const opensAppOnly = match.deep_link === false;

        return (
          <div
            key={`${match.service_name}-${match.content_id}`}
            className="w-40 shrink-0 snap-start overflow-hidden rounded-xl border border-white/10 bg-[#14141f]"
          >
            {match.poster_url && (
              <Poster url={match.poster_url} title={cleanTitle(match.title)} />
            )}

            <div className="p-3">
              {/* Service badge */}
              <div
                className="mb-2 inline-block rounded-md px-2 py-0.5 text-xs font-semibold text-white"
                style={{ backgroundColor: color }}
              >
                {displayName}
              </div>

              {/* Title */}
              <p className="mb-1 line-clamp-2 text-sm font-medium leading-tight text-slate-200">
                {cleanTitle(match.title)}
              </p>

              {/* Season/episode, when the request carried them */}
              {seasonEpisode(match) && (
                <p className="text-xs text-slate-500">{seasonEpisode(match)}</p>
              )}

              {/* Play button */}
              <button
                onClick={() => onLaunch(match)}
                disabled={isLaunching}
                className="mt-3 w-full cursor-pointer rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:cursor-wait disabled:opacity-50"
              >
                {isLaunching
                  ? "Launching..."
                  : opensAppOnly
                    ? "Open App"
                    : canResume
                      ? "Resume"
                      : "Play"}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
