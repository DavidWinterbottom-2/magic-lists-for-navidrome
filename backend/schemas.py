from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Union
from datetime import datetime

class Artist(BaseModel):
    """Schema for Navidrome artist"""
    id: str
    name: str
    album_count: int = 0
    song_count: int = 0

class CreatePlaylistRequest(BaseModel):
    """Request schema for creating a playlist"""
    artist_ids: List[str]
    playlist_name: Optional[str] = None  # Optional, will auto-generate if not provided
    refresh_frequency: str = "none"  # "none", "daily", "weekly", "monthly"
    playlist_length: int = 25  # Number of tracks to include
    library_ids: List[str] = []  # List of library IDs to filter tracks

class CreateGenrePlaylistRequest(BaseModel):
    """Request schema for creating a genre mix playlist"""
    genre: str
    playlist_name: Optional[str] = None  # Optional, will auto-generate if not provided
    refresh_frequency: str = "none"  # "none", "daily", "weekly", "monthly"
    playlist_length: int = 25  # Number of tracks to include
    library_ids: List[str] = []  # List of library IDs to filter tracks

class CreateRadioPlaylistRequest(BaseModel):
    """Request schema for creating a Radio playlist seeded from an artist or song"""
    seed_type: str = "artist"  # "artist" or "song"
    seed_id: str  # Navidrome artist ID or song ID depending on seed_type
    playlist_name: Optional[str] = None  # Optional, auto-generated if not provided
    refresh_frequency: str = "none"  # "none", "daily", "weekly", "monthly"
    playlist_length: int = 25  # Number of tracks to include
    library_ids: List[str] = []  # List of library IDs to filter tracks

class RecreatePlaylistRequest(BaseModel):
    """Options for a manual rebuild.

    Both are optional and default to leaving the playlist's existing settings
    alone. They exist because a rebuild is the natural moment to change your
    mind — particularly to raise the length again after a short build, which
    otherwise needs deleting and recreating the playlist from scratch.
    """
    playlist_length: Optional[int] = None       # None = keep the stored length
    refresh_frequency: Optional[str] = None     # None = keep the current schedule


class AlbumSuggestion(BaseModel):
    """Schema for a suggested album not currently in the library"""
    artist: str
    album: str
    year: Optional[int] = None
    reason: Optional[str] = None

class PlaylistTrack(BaseModel):
    """A track as stored on a playlist.

    Artist and album default to empty so playlists written before tracks
    carried them still validate — see `normalise_stored_songs` in main.py.
    """
    title: str
    artist: str = ""
    album: str = ""


class Playlist(BaseModel):
    """Schema for a stored playlist"""
    id: int
    artist_id: str
    playlist_name: str
    # Rows written before tracks carried artist/album hold bare title strings.
    songs: List[Union[PlaylistTrack, str]] = []
    reasoning: Optional[str] = None
    navidrome_playlist_id: Optional[str] = None
    library_ids: List[str] = []
    created_at: str
    updated_at: str

class Song(BaseModel):
    """Schema for a song"""
    id: str
    title: str
    artist: str
    album: str
    duration: Optional[int] = None
    track_number: Optional[int] = None

class PlaylistResponse(BaseModel):
    """Response schema for playlist operations"""
    playlist: Playlist
    message: str

class RediscoverTrack(BaseModel):
    """Schema for a Re-Discover Weekly track"""
    id: str
    title: str
    artist: str
    album: str
    score: float
    historical_plays: int
    # Computed as a day count everywhere in rediscover.py, but one legacy path
    # still yields the string "30+". Declared `str` alone, this rejected the
    # int and returned a 500 for any library holding never-played tracks —
    # which is most of them.
    days_since_last_play: Union[int, str]

class RediscoverWeeklyResponse(BaseModel):
    """Response schema for Re-Discover Weekly"""
    tracks: List[RediscoverTrack]
    total_tracks: int
    message: str

class RediscoverWeeklyV2Response(BaseModel):
    """Response schema for Re-Discover Weekly v2.0"""
    name: str
    tracks: List[Dict[str, Any]]
    theme: str
    mode: str
    reasoning: str
    user_id: str
    server_id: str
    generated_at: str
    is_fallback: Optional[bool] = False

class CreateRediscoverPlaylistRequest(BaseModel):
    """Request schema for creating a Re-Discover Weekly playlist"""
    refresh_frequency: str = "weekly"  # "daily", "weekly", "monthly"
    playlist_length: int = 25  # Number of tracks to include
    library_ids: List[str] = []  # List of library IDs to filter tracks

class ScheduledPlaylist(BaseModel):
    """Schema for a scheduled playlist"""
    id: int
    playlist_type: str  # "rediscover_weekly"
    navidrome_playlist_id: str
    refresh_frequency: str
    next_refresh: str
    created_at: str
    updated_at: str

class PlaylistWithScheduleInfo(BaseModel):
    """Schema for playlist with schedule information"""
    id: int
    artist_id: str
    playlist_name: str
    songs: List[Union[PlaylistTrack, str]]
    created_at: str
    updated_at: str
    navidrome_playlist_id: Optional[str] = None
    refresh_frequency: Optional[str] = None
    next_refresh: Optional[str] = None
    playlist_type: Optional[str] = None