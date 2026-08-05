"""
Smart Track Scoring & Filtering for "This Is" Playlists

Optimizes payload size for LLM compatibility and token cost efficiency by intelligently 
scoring and filtering source tracks based on user listening behavior.
"""

import random
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Any

# Fraction of a pool that must have been played before engagement scoring is
# trusted to pick the whole candidate set. Below this, play counts and recency
# say more about which corner of the library has been visited than about which
# tracks belong together, so part of the set is drawn at random instead and
# style similarity (the AI's job) carries the station.
FULL_ENGAGEMENT_COVERAGE = 0.30


def mark_starred_loved(tracks: List[Dict]) -> int:
    """Treat a Navidrome-starred track as 'loved' for engagement scoring.

    `score_tracks_by_user_engagement` awards +50 for `track['loved']`, but the
    candidate tracks only ever carry Navidrome's own favourite flag
    (`local_library_likes`, from the Subsonic `starred` field). Navidrome does not
    sync that heart to Last.fm, so the listener's real favourites — including any
    hearted from a client like Amperfy — live here, not in Last.fm's loved tracks.
    Map them onto `loved` so they actually count. Returns the number newly marked.
    """
    marked = 0
    for track in tracks:
        if track.get("loved"):
            continue
        if track.get("local_library_likes") or track.get("starred"):
            track["loved"] = True
            marked += 1
    return marked


def measure_play_coverage(tracks: List[Dict]) -> float:
    """Fraction of these tracks that have ever been played."""
    if not tracks:
        return 0.0
    played = sum(1 for track in tracks if (track.get('play_count') or 0) > 0)
    return played / len(tracks)


def effective_library_stats(tracks: List[Dict], library_stats: Optional[Dict] = None) -> Dict:
    """Normalisation stats derived from the tracks in hand.

    `NavidromeClient.get_library_stats` can't read a real maximum out of
    Subsonic, so it *estimates* one — `max(100, total_tracks * 0.1)` — and on a
    server that reports no total it falls back to a flat 100. Normalising real
    play counts against an invented 100 makes the play signal almost weightless
    on a young library: a track played 5 times scores 5, against a +50 bonus for
    being starred.

    Measuring the pool instead means the busiest track in it scores full marks,
    whatever scale this particular library is on.
    """
    stats = dict(library_stats or {})
    pool_max_plays = max((track.get('play_count') or 0) for track in tracks) if tracks else 0
    if pool_max_plays > 0:
        stats['max_play_count'] = pool_max_plays
    elif not stats.get('max_play_count'):
        stats['max_play_count'] = 1
    return stats


def select_with_tiebreak(
    scored_tracks: List[Tuple[float, Dict]],
    count: int,
    rng: Optional[random.Random] = None
) -> List[Dict]:
    """Take the `count` best-scoring tracks, breaking ties at random.

    A plain `scored[:count]` slice looks fine until most of the pool scores the
    same — which is the normal case for a library that hasn't been played much,
    where the great majority of tracks score exactly 0. Python's sort is stable,
    so the identical subset was chosen on every rebuild, the AI saw the same
    candidates every time, and stations came back looking unchanged no matter
    what had been added to the library.

    Tracks that genuinely outscore the cutoff are still always kept; only the
    tied block at the boundary is sampled.
    """
    rng = rng or random
    if count >= len(scored_tracks):
        return [track for _, track in scored_tracks]
    if count <= 0:
        return []

    cutoff = scored_tracks[count - 1][0]
    above = [track for score, track in scored_tracks if score > cutoff]
    tied = [track for score, track in scored_tracks if score == cutoff]
    rng.shuffle(tied)
    return above + tied[:max(0, count - len(above))]


def score_tracks_by_user_engagement(tracks: List[Dict], library_stats: Dict) -> List[Tuple[float, Dict]]:
    """
    Score tracks based on user's listening behavior.
    Returns list of (score, track) tuples sorted by score descending.
    
    Args:
        tracks: List of track objects to score
        library_stats: Dict containing user's library statistics:
            - max_play_count: Highest play count in library
            - max_playlist_appearances: Most appearances any track has
            
    Returns:
        List of (score, track) tuples, sorted descending by score
    """
    scored_tracks = []
    
    # Initialize counters for detailed logging
    engagement_stats = {
        'total_tracks': len(tracks),
        'loved_tracks': 0,
        'rated_tracks': 0,
        'tracks_with_plays': 0,
        'tracks_in_playlists': 0,
        'recent_tracks': 0,
        'total_play_count': 0,
        'total_playlist_appearances': 0,
        'max_score': 0,
        'min_score': float('inf'),
        'avg_score': 0
    }
    
    total_score = 0
    
    for track in tracks:
        score = 0.0
        track_breakdown = {}  # For detailed per-track logging if needed
        
        # Play count (normalize to 0-100 scale)
        play_count = track.get('play_count', 0)
        if play_count > 0:
            engagement_stats['tracks_with_plays'] += 1
            engagement_stats['total_play_count'] += play_count
            
        if library_stats.get('max_play_count', 0) > 0:
            normalized_plays = (play_count / library_stats['max_play_count']) * 100
            score += normalized_plays
            track_breakdown['play_score'] = normalized_plays
        
        # Loved/hearted tracks (high value binary signal)
        if track.get('loved', False) or track.get('favorited', False):
            score += 50
            engagement_stats['loved_tracks'] += 1
            track_breakdown['loved_bonus'] = 50
        
        # Star ratings (0-5 scale, normalize to 0-50)
        rating = track.get('rating', 0)
        if rating > 0:
            engagement_stats['rated_tracks'] += 1
            score += rating * 10
            track_breakdown['rating_score'] = rating * 10
        
        # Playlist appearances (cap at 50 to avoid over-weighting)
        playlist_count = track.get('playlist_appearances', 0)
        if playlist_count > 0:
            engagement_stats['tracks_in_playlists'] += 1
            engagement_stats['total_playlist_appearances'] += playlist_count
            
        playlist_score = min(playlist_count * 5, 50)
        score += playlist_score
        track_breakdown['playlist_score'] = playlist_score
        
        # Optional: Recency bonus (tracks played in last 30 days)
        # Only include if last_played data is available
        if track.get('last_played'):
            try:
                # Handle both string and datetime objects
                if isinstance(track['last_played'], str):
                    last_played_date = datetime.fromisoformat(track['last_played'].replace('Z', '+00:00'))
                else:
                    last_played_date = track['last_played']
                
                days_since = (datetime.now() - last_played_date.replace(tzinfo=None)).days
                if days_since <= 30:
                    recency_bonus = max(0, 30 - days_since)
                    score += recency_bonus
                    engagement_stats['recent_tracks'] += 1
                    track_breakdown['recency_bonus'] = recency_bonus
            except (ValueError, TypeError):
                # Skip recency bonus if date parsing fails
                pass
        
        scored_tracks.append((score, track))
        
        # Update score statistics
        total_score += score
        engagement_stats['max_score'] = max(engagement_stats['max_score'], score)
        engagement_stats['min_score'] = min(engagement_stats['min_score'], score)
    
    # Calculate average score
    engagement_stats['avg_score'] = total_score / len(tracks) if tracks else 0
    if engagement_stats['min_score'] == float('inf'):
        engagement_stats['min_score'] = 0
    
    # Sort by score descending
    scored_tracks.sort(reverse=True, key=lambda x: x[0])
    
    # Log detailed engagement statistics
    print(f"🎯 SCORING ANALYSIS:")
    print(f"   📊 Sourced {engagement_stats['total_tracks']} tracks for analysis")
    print(f"   ❤️  Found {engagement_stats['loved_tracks']} loved/favorited tracks")
    print(f"   ⭐ Found {engagement_stats['rated_tracks']} rated tracks")
    print(f"   🎵 Found {engagement_stats['tracks_with_plays']} tracks with play counts (total: {engagement_stats['total_play_count']} plays)")
    print(f"   📋 Found {engagement_stats['tracks_in_playlists']} tracks in playlists (total: {engagement_stats['total_playlist_appearances']} appearances)")
    print(f"   🕐 Found {engagement_stats['recent_tracks']} recently played tracks (last 30 days)")
    print(f"   🏆 Score range: {engagement_stats['max_score']:.1f} - {engagement_stats['min_score']:.1f} (avg: {engagement_stats['avg_score']:.1f})")
    
    return scored_tracks


def calculate_filter_threshold(target_playlist_size: int) -> int:
    """
    Calculate optimal multiplier for filtering source tracks.
    
    Rationale: As playlist size increases, we can use a lower multiplier
    because probability of capturing high-quality tracks increases.
    
    Args:
        target_playlist_size: Desired number of tracks in final playlist
        
    Returns:
        int: Multiplier for filtering (e.g., 10 means keep 10x target size)
    """
    if target_playlist_size <= 25:
        return 10  # 25 tracks -> keep top 250
    elif target_playlist_size <= 50:
        return 8   # 50 tracks -> keep top 400
    elif target_playlist_size <= 100:
        return 6   # 100 tracks -> keep top 600
    else:
        # For larger playlists, use diminishing multiplier
        # Cap at 5x to balance quality and token efficiency
        return max(5, int(600 / target_playlist_size * 6))


def should_apply_smart_filtering(source_tracks: List[Dict], target_playlist_size: int) -> bool:
    """
    Determine if smart filtering should be applied based on track count and target size.
    
    Args:
        source_tracks: List of all available tracks for the artist
        target_playlist_size: Desired number of tracks in final playlist
        
    Returns:
        bool: True if filtering should be applied
    """
    threshold_multiplier = calculate_filter_threshold(target_playlist_size)
    threshold = target_playlist_size * threshold_multiplier
    
    return len(source_tracks) > threshold


def filter_tracks_by_engagement(
    tracks: List[Dict], 
    target_playlist_size: int, 
    library_stats: Dict
) -> List[Dict]:
    """
    Apply smart filtering to tracks if needed, returning filtered subset.
    
    Args:
        tracks: List of all available tracks
        target_playlist_size: Desired number of tracks in final playlist
        library_stats: Library statistics for scoring
        
    Returns:
        List[Dict]: Filtered tracks (or original if filtering not needed)
    """
    # Check if filtering is needed
    if not should_apply_smart_filtering(tracks, target_playlist_size):
        return tracks
    
    # Calculate how many tracks to keep
    threshold_multiplier = calculate_filter_threshold(target_playlist_size)
    max_tracks_to_keep = target_playlist_size * threshold_multiplier
    
    # Score and filter tracks, normalising against the pool in hand
    stats = effective_library_stats(tracks, library_stats)
    scored_tracks = score_tracks_by_user_engagement(tracks, stats)

    # Ties are broken at random for the same reason as the "This Is" filter:
    # a stable sort over a mostly-unplayed pool returns the same tracks forever.
    return select_with_tiebreak(scored_tracks, max_tracks_to_keep)


def filter_tracks_for_this_is_playlist(
    source_tracks: List[Dict], 
    target_playlist_size: int, 
    library_stats: Dict
) -> Tuple[List[Dict], Dict[str, Any]]:
    """
    Filter source tracks for "This Is" playlists using engagement scoring.
    
    Args:
        source_tracks: Full list of tracks matching artist/criteria
        target_playlist_size: Desired final playlist length
        library_stats: User's library statistics for normalization
        
    Returns:
        tuple: (filtered_tracks, filter_metadata)
            - filtered_tracks: Subset of tracks to send to LLM
            - filter_metadata: Dict with info about filtering for logging/UI
    """
    threshold_multiplier = calculate_filter_threshold(target_playlist_size)
    threshold_count = target_playlist_size * threshold_multiplier
    
    # Only filter if source tracks exceed threshold
    if len(source_tracks) <= threshold_count:
        return source_tracks, {
            'filtered': False,
            'reason': 'below_threshold',
            'source_count': len(source_tracks),
            'sent_count': len(source_tracks)
        }
    
    # Normalise against what this pool actually looks like, not an estimate
    stats = effective_library_stats(source_tracks, library_stats)
    scored_tracks = score_tracks_by_user_engagement(source_tracks, stats)

    # How far to trust engagement scoring at all. On a barely-played library the
    # top of the ranking is just "what I happened to listen to recently", which
    # produces a narrow, repetitive station; so only part of the set is chosen by
    # score and the rest is drawn at random from everything else, letting the AI
    # pick on style. As the library gets played this fades back to pure scoring.
    coverage = measure_play_coverage(source_tracks)
    engagement_share = min(1.0, coverage / FULL_ENGAGEMENT_COVERAGE)
    engagement_count = int(round(threshold_count * engagement_share))

    filtered_tracks = select_with_tiebreak(scored_tracks, engagement_count)

    # Fill the remainder at random from whatever engagement scoring didn't take
    if len(filtered_tracks) < threshold_count:
        chosen = {id(track) for track in filtered_tracks}
        remainder = [track for _, track in scored_tracks if id(track) not in chosen]
        random.shuffle(remainder)
        filtered_tracks.extend(remainder[:threshold_count - len(filtered_tracks)])

    print(
        f"   🎲 Play coverage {coverage:.0%} → {engagement_count}/{threshold_count} "
        f"by engagement, {threshold_count - engagement_count} sampled"
    )

    # Log filtering decision and final payload
    print(f"🎯 FILTERING DECISION:")
    print(f"   🎯 Threshold: {threshold_count} tracks (target: {target_playlist_size} × {threshold_multiplier}x multiplier)")
    print(f"   ✂️  Filtered {len(source_tracks)} → {len(filtered_tracks)} tracks for LLM payload")
    print(f"   📤 Payload reduction: {((len(source_tracks) - len(filtered_tracks)) / len(source_tracks) * 100):.1f}%")
    
    # Metadata for logging and user feedback
    filter_metadata = {
        'filtered': True,
        'source_count': len(source_tracks),
        'sent_count': len(filtered_tracks),
        'threshold_multiplier': threshold_multiplier,
        'play_coverage': coverage,
        'engagement_count': engagement_count,
        'sampled_count': len(filtered_tracks) - engagement_count,
        'score_range': {
            'highest': scored_tracks[0][0] if scored_tracks else 0,
            'lowest': scored_tracks[threshold_count-1][0] if len(scored_tracks) >= threshold_count else 0,
            'cutoff': scored_tracks[threshold_count][0] if len(scored_tracks) > threshold_count else 0
        }
    }
    
    return filtered_tracks, filter_metadata