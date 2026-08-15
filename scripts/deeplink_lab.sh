#!/usr/bin/env bash
# Interactive Roku deep-link experiment harness.
#
# Steps through candidate launch configurations for channels whose deep-link
# parameter format is undocumented (Apple TV, Max). For each test it presses
# Home, fires the launch, then polls query/media-player so you can correlate
# what you see on the TV with what the player reports.
#
# Usage:
#   ./scripts/deeplink_lab.sh            # pick tests from the menu
#   ./scripts/deeplink_lab.sh all       # run every test in order
#   ./scripts/deeplink_lab.sh 61322 "contentId=abc&mediaType=movie"  # one-off
#
# ROKU_IP overrides the target device (default 192.168.1.252).

set -u
ROKU_IP="${ROKU_IP:-192.168.1.252}"
R="http://${ROKU_IP}:8060"

# Test fixtures:
#   Severance (Apple TV) show id + S1E1 "Good News About Hell" episode id,
#   scraped from tv.apple.com.
ATV_SHOW="umc.cmc.1srk2goyh2q2zdxcx605w8vtx"
ATV_EP="umc.cmc.s80mx1ic96pu6ewupz8pfasf"
#   The Pitt (Max) show id + S1E1 episode id, scraped from max.com
#   (/shows/pitt-2024/s1/<show>/e1-700-am/<episode>).
MAX_SHOW="e6e7bad9-d48d-4434-b334-7c651ffc4bdf"
MAX_EP="e4b915fb-5e6b-42b8-97ac-90ec7d0e3147"

# label|channel_id|query string
TESTS=(
  "MAX  episode-id  mediaType=episode|61322|contentId=${MAX_EP}&mediaType=episode"
  "MAX  episode-id  mediaType=movie|61322|contentId=${MAX_EP}&mediaType=movie"
  "MAX  episode-id  mediaType=series|61322|contentId=${MAX_EP}&mediaType=series"
  "MAX  show-id     mediaType=series (known: 'video not available')|61322|contentId=${MAX_SHOW}&mediaType=series"
  "MAX  episode-id  contentID casing|61322|contentID=${MAX_EP}&mediaType=episode"
  "ATV  episode-id  mediaType=episode|551012|contentId=${ATV_EP}&mediaType=episode"
  "ATV  episode-id  + showId param|551012|contentId=${ATV_EP}&showId=${ATV_SHOW}&mediaType=episode"
  "ATV  show-id     mediaType=series (known fail)|551012|contentId=${ATV_SHOW}&mediaType=series"
  "ATV  show-id     contentID casing|551012|contentID=${ATV_SHOW}&mediaType=series"
  "ATV  episode-id  mediaType=movie|551012|contentId=${ATV_EP}&mediaType=movie"
)

player_state() {
  curl -fsS --max-time 5 "$R/query/media-player" 2>/dev/null |
    grep -o 'state="[^"]*"\|<position>[^<]*</position>\|<duration>[^<]*</duration>' |
    tr '\n' ' '
}

run_test() {
  local channel="$1" query="$2" label="$3"
  echo
  echo "=== $label"
  echo "    POST /launch/$channel?$query"
  curl -fsS -X POST "$R/keypress/Home" -d '' >/dev/null && sleep 4
  if ! curl -fsS -X POST "$R/launch/$channel?$query" -d ''; then
    echo "    launch request FAILED (see curl error above)"
    return
  fi
  for t in 5 10 15 20 25 30 35 40; do
    sleep 5
    echo "    t=${t}s  $(player_state)"
  done
  echo "--- What happened on screen? (note it down, then continue)"
}

if [[ $# -eq 2 && $1 =~ ^[0-9]+$ ]]; then
  run_test "$1" "$2" "one-off: channel $1"
  exit 0
fi

if [[ "${1:-}" == "all" ]]; then
  for entry in "${TESTS[@]}"; do
    IFS='|' read -r label channel query <<<"$entry"
    run_test "$channel" "$query" "$label"
    read -rp "Press Enter for next test (Ctrl-C to stop)..."
  done
  exit 0
fi

while true; do
  echo
  echo "Roku deep-link lab — target $ROKU_IP"
  for i in "${!TESTS[@]}"; do
    IFS='|' read -r label _ _ <<<"${TESTS[$i]}"
    printf '  %2d) %s\n' "$((i + 1))" "$label"
  done
  echo "   q) quit"
  read -rp "Run which test? " choice
  [[ "$choice" == "q" ]] && exit 0
  if [[ "$choice" =~ ^[0-9]+$ ]] && ((choice >= 1 && choice <= ${#TESTS[@]})); then
    IFS='|' read -r label channel query <<<"${TESTS[$((choice - 1))]}"
    run_test "$channel" "$query" "$label"
  else
    echo "Invalid choice."
  fi
done
