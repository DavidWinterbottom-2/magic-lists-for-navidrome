#!/usr/bin/env bash
# Read-only probe for #65 (task 0.1). Usage:
#   LB_USER=<listenbrainz user> SEED_ARTIST_MBID=<mbid> ./probe.sh
# Optional: NAVIDROME_URL, NAVIDROME_USER, NAVIDROME_PASS, SEED_ARTIST_ID to test A4.
# Prints status + a trimmed body. Never prints credentials.
set -u
LB="${LISTENBRAINZ_URL:-https://api.listenbrainz.org}"
: "${LB_USER:?set LB_USER}"; : "${SEED_ARTIST_MBID:?set SEED_ARTIST_MBID}"
show() { echo "== $1"; curl -sS -m 20 -w '\nHTTP %{http_code}\n' "$2" | head -c 1500; echo; }
show "A1 top recordings" "$LB/1/stats/user/$LB_USER/recordings?range=half_yearly&count=3"
show "A2 loved (metadata)" "$LB/1/feedback/user/$LB_USER/get-feedback?score=1&count=3&metadata=true"
show "A3 similar artists (labs)" "https://labs.api.listenbrainz.org/similar-artists/json?artist_mbids=$SEED_ARTIST_MBID&algorithm=session_based_days_9000_session_300_contribution_5_threshold_15_limit_50_skip_30"
show "health candidate" "$LB/1/user/$LB_USER/listen-count"
if [ -n "${NAVIDROME_URL:-}" ] && [ -n "${SEED_ARTIST_ID:-}" ]; then
  echo "== A4 Navidrome getArtist musicBrainzId"
  salt=$(openssl rand -hex 6); tok=$(printf '%s%s' "$NAVIDROME_PASS" "$salt" | md5sum | cut -d' ' -f1)
  curl -sS -m 20 "$NAVIDROME_URL/rest/getArtist?u=$NAVIDROME_USER&t=$tok&s=$salt&v=1.16.1&c=probe&f=json&id=$SEED_ARTIST_ID" | grep -o '"musicBrainzId":"[^"]*"' | head -1 || echo "no musicBrainzId field"
fi
