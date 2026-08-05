from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi import Query
import uvicorn
import os
import logging
import logging.handlers
from typing import List, Optional
from datetime import datetime, timedelta
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio

# Load environment variables first
load_dotenv()

# Get log level from environment (ERROR=minimal, INFO=normal, DEBUG=verbose)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Configure logging for scheduler activities with rotation
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.handlers.RotatingFileHandler(
            'scheduler.log',
            maxBytes=5*1024*1024,  # 5MB per file
            backupCount=2,         # Keep 2 old files (total ~10MB)
            encoding='utf-8'
        ),
        logging.StreamHandler()  # Also log to console
    ]
)

# Create a specific logger for scheduler activities
scheduler_logger = logging.getLogger('scheduler')

# Reduce httpx logging verbosity to avoid cluttering scheduler.log
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

from .navidrome_client import NavidromeClient
from .ai_client import AIClient
from .database import DatabaseManager, get_db
from .schemas import CreatePlaylistRequest, CreateGenrePlaylistRequest, CreateRadioPlaylistRequest, RecreatePlaylistRequest, Playlist, RediscoverWeeklyResponse, RediscoverWeeklyV2Response, CreateRediscoverPlaylistRequest, PlaylistWithScheduleInfo
from .recipe_manager import recipe_manager
from .rediscover import RediscoverWeekly, ReDiscoverV2Processor
from .radio import (
    RadioProcessor, build_shortfall, cap_seed_artist, count_distinct_artists,
    lidarr_add_url, promote_seed_first
)
from .track_scoring import filter_tracks_for_this_is_playlist
from .lastfm_client import LastfmClient, mark_loved
# SYSTEM CHECK FEATURE - START
from .services.health_check_service import HealthCheckService
# SYSTEM CHECK FEATURE - END

from . import auth

app = FastAPI(title="MagicLists Navidrome MVP")

# Gate the whole app behind Microsoft Entra OIDC before any routes are defined,
# so the Navidrome/AI credentials it holds are never reachable anonymously once
# it's public. No-ops when AUTH_DISABLED (the default) — a trusted LAN is
# unchanged. See backend/auth.py.
auth.install(app)


@app.get("/health", include_in_schema=False)
async def health():
    """Public liveness probe for the container healthcheck and reverse proxy.

    Deliberately trivial (no Navidrome/AI/DB calls) and exempt from auth, so it
    stays a fast, dependency-free 200 even when the app is gated."""
    return {"status": "ok"}

@app.on_event("startup")
async def startup_event():
    """Initialize scheduler on app startup"""
    global scheduler, system_check_passed, system_check_results
    scheduler = AsyncIOScheduler()
    scheduler.start()
    scheduler_logger.info("✅ Scheduler started successfully")
    # Auto-start the cron job
    await start_scheduler_job()
    scheduler_logger.info("✅ Cron job auto-started on application startup")
    
    # SYSTEM CHECK FEATURE - START
    # Run system checks on startup
    try:
        health_service = HealthCheckService()
        system_check_results = await health_service.run_checks()
        system_check_passed = system_check_results.get("all_passed", False)
        
        if system_check_passed:
            scheduler_logger.info("✅ System health checks passed on startup")
        else:
            scheduler_logger.warning("⚠️ System health checks failed on startup - user will be redirected to system check page")
            
        # Log individual check results with enhanced AI provider logging
        for check in system_check_results.get("checks", []):
            status_emoji = "✅" if check["status"] == "success" else "⚠️" if check["status"] == "warning" else "ℹ️" if check["status"] == "info" else "❌"
            
            # Enhanced logging for AI Provider checks
            if "AI Provider" in check["name"]:
                ai_provider = os.getenv("AI_PROVIDER", "openrouter")
                if check["status"] == "success":
                    # Extract model from success message (e.g., "service reachable (model: llama3.2)")
                    if "model:" in check["message"]:
                        model_part = check["message"].split("model: ")[1].rstrip(")")
                        scheduler_logger.info(f"🤖 AI Provider: {ai_provider.title()} with model '{model_part}' - Ready")
                    else:
                        scheduler_logger.info(f"🤖 AI Provider: {ai_provider.title()} - Ready")
                elif check["status"] == "warning":
                    if "not set" in check["message"]:
                        scheduler_logger.info(f"🤖 AI Provider: {ai_provider.title()} - No API key (using fallback algorithms)")
                    else:
                        scheduler_logger.warning(f"🤖 AI Provider: {ai_provider.title()} - {check['message']}")
                elif check["status"] == "error":
                    scheduler_logger.error(f"🤖 AI Provider: {ai_provider.title()} - {check['message']}")
            else:
                # Standard logging for other checks
                scheduler_logger.info(f"{status_emoji} {check['name']}: {check['status']}")
            
    except Exception as e:
        scheduler_logger.error(f"❌ Failed to run system checks on startup: {e}")
        system_check_passed = False
        system_check_results = {
            "all_passed": False,
            "checks": [{
                "name": "System Check Service",
                "status": "error", 
                "message": f"Failed to run health checks: {str(e)}",
                "suggestion": "Check application logs and restart the service"
            }]
        }
    # SYSTEM CHECK FEATURE - END

@app.on_event("shutdown") 
async def shutdown_event():
    """Cleanup scheduler on app shutdown"""
    global scheduler
    if scheduler:
        scheduler.shutdown()
        scheduler_logger.info("🛑 Scheduler shutdown completed")

# Mount static files
class RevalidatingStaticFiles(StaticFiles):
    """Serve static assets with `Cache-Control: no-cache`.

    Starlette already sends ETag/Last-Modified, but without Cache-Control a
    browser is free to reuse a cached copy without revalidating. That means a
    changed app.js can keep serving stale while index.html renders fresh, which
    silently breaks the SPA (an unknown page id hides every content panel).

    "no-cache" does not disable caching - it requires revalidation, so unchanged
    files still come back as a cheap 304.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", RevalidatingStaticFiles(directory="frontend/static"), name="static")


# A service worker may only control URLs at or below its own path, so the app
# shell's worker has to be served from the root rather than from /static.
# The manifest sits alongside it for the same reason (its scope is "/").
@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    from fastapi.responses import FileResponse
    return FileResponse(
        "frontend/static/sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"}
    )


@app.get("/manifest.webmanifest", include_in_schema=False)
async def web_manifest():
    from fastapi.responses import FileResponse
    return FileResponse(
        "frontend/static/manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"}
    )


# Templates
templates = Jinja2Templates(directory="frontend/templates")


def static_version() -> str:
    """Cache-busting token for /static/app.js, derived from its mtime.

    The template used to hardcode "?v=1.0.0", so the URL never changed and
    browsers kept serving a cached app.js after every deploy. Deriving the
    token from the file means the URL changes whenever the file does.
    """
    try:
        return str(int(os.path.getmtime("frontend/static/app.js")))
    except OSError:
        return "0"


def analytics_config() -> dict:
    """Where to report usage analytics, if anywhere. Umami-shaped.

    Upstream hardcoded a tag pointing at the original author's analytics
    instance, so a fork silently reported every playlist build — counts, seed
    type, AI provider and model — to a third party. Making the collector
    configuration means it points at an instance you run, and collects nothing
    at all until you say where.

    Both values are required: a script URL with no website id (or vice versa)
    can't report anywhere useful, so it's treated as "off" rather than
    half-wired and silently dropping events.
    """
    script_url = os.getenv("ANALYTICS_SCRIPT_URL", "").strip()
    website_id = os.getenv("ANALYTICS_WEBSITE_ID", "").strip()
    if not script_url or not website_id:
        return {"analytics_script_url": None, "analytics_website_id": None}
    return {"analytics_script_url": script_url, "analytics_website_id": website_id}


def render_index(request: Request) -> HTMLResponse:
    """Render the SPA shell with a cache-busting version for its assets"""
    return templates.TemplateResponse(
        request, "index.html", {"static_version": static_version(), **analytics_config()}
    )


def summarise_track(track: dict) -> dict:
    """Reduce a track to what Manage Playlists needs to display it.

    Playlists used to store a bare list of title strings, which left the UI
    unable to tell two songs of the same name apart or show what album a track
    came from. Storing the artist and album alongside costs a few bytes per
    track and avoids re-querying Navidrome to render the list.
    """
    return {
        "title": track.get("title") or "Unknown",
        "artist": track.get("artist") or "",
        "album": track.get("album") or "",
    }


def summarise_tracks(tracks: list, order: list = None) -> list:
    """Summarise tracks for storage, optionally reordered to `order` (track ids).

    `order` is the AI's curated sequence; tracks it referenced but that aren't
    in `tracks` are skipped, matching the previous title-mapping behaviour.
    """
    if order is None:
        return [summarise_track(track) for track in tracks]

    by_id = {track["id"]: track for track in tracks}
    return [summarise_track(by_id[tid]) for tid in order if tid in by_id]


def normalise_stored_songs(songs) -> list:
    """Present stored songs uniformly, whatever shape the row was written in.

    Playlists created before tracks carried artist/album are plain strings, so
    they are widened here rather than migrated — a stored title is all the
    information those rows ever had, and re-deriving the rest would mean
    refetching every playlist from Navidrome.
    """
    normalised = []
    for song in songs or []:
        if isinstance(song, dict):
            normalised.append({
                "title": song.get("title") or "Unknown",
                "artist": song.get("artist") or "",
                "album": song.get("album") or "",
            })
        else:
            normalised.append({"title": str(song), "artist": "", "album": ""})
    return normalised


def song_labels(songs) -> list:
    """Flat "Title — Artist" strings, for the refresh prompts' variety context.

    Those prompts interpolate the previous tracklist so the model can avoid
    repeating it. They used to join the stored title strings directly, which
    breaks now that rows store dicts — and naming the artist makes the
    constraint more useful to the model anyway.
    """
    labels = []
    for song in normalise_stored_songs(songs):
        labels.append(f"{song['title']} — {song['artist']}" if song["artist"] else song["title"])
    return labels

# Initialize clients (lazy loading)
navidrome_client = None
ai_client = None
lastfm_client = None

# Initialize scheduler (will be started on app startup)
scheduler = None

# SYSTEM CHECK FEATURE - START
# App state to track system check results
system_check_passed = False
system_check_results = None
# SYSTEM CHECK FEATURE - END

def get_navidrome_client():
    global navidrome_client
    if navidrome_client is None:
        navidrome_client = NavidromeClient()
    return navidrome_client

def get_ai_client():
    global ai_client
    if ai_client is None:
        ai_client = AIClient()
    return ai_client


def get_lastfm_client():
    global lastfm_client
    if lastfm_client is None:
        lastfm_client = LastfmClient()
    return lastfm_client


async def apply_loved_signal(tracks):
    """Mark candidate tracks the listener has loved on Last.fm, in place.

    Lights up the +50 "loved" bonus in engagement scoring, which nothing else in
    the app populates. A no-op when Last.fm isn't configured (or the profile hides
    its data), so every caller keeps its existing behaviour unchanged.
    """
    client = get_lastfm_client()
    if not client.user_enabled:
        return
    loved_keys = await client.loved_track_keys()
    marked = mark_loved(tracks, loved_keys)
    if marked:
        scheduler_logger.info(f"❤️ Last.fm: marked {marked} loved track(s) in candidate pool")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serve the main HTML page"""
    # SYSTEM CHECK FEATURE - START
    # Redirect to system check if checks haven't passed
    if not system_check_passed:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/system-check", status_code=302)
    # SYSTEM CHECK FEATURE - END
    
    return render_index(request)

# SYSTEM CHECK FEATURE - START
@app.get("/system-check", response_class=HTMLResponse)
async def system_check_page(request: Request):
    """Serve the system check page"""
    return render_index(request)
# SYSTEM CHECK FEATURE - END

@app.get("/api/artists")
async def get_artists(library_id: List[str] = Query(None)):
    """Get list of artists from Navidrome"""
    try:
        client = get_navidrome_client()
        artists = await client.get_artists(library_id)
        return artists
    except Exception as e:
        error_msg = str(e)
        # Check if it's an authentication error and return appropriate status code
        if "Invalid username or password" in error_msg or "No authentication method available" in error_msg:
            raise HTTPException(status_code=401, detail=error_msg)
        elif "Network error" in error_msg or "connecting to Navidrome" in error_msg:
            raise HTTPException(status_code=503, detail=f"Cannot connect to Navidrome server: {error_msg}")
        else:
            raise HTTPException(status_code=500, detail=f"Failed to fetch artists: {error_msg}")

@app.get("/api/genres")
async def get_genres(library_id: List[str] = Query(None)):
    """Get list of genres from Navidrome"""
    try:
        client = get_navidrome_client()
        genres = await client.get_genres(library_id)
        return genres
    except Exception as e:
        error_msg = str(e)
        # Check if it's an authentication error and return appropriate status code
        if "Invalid username or password" in error_msg or "No authentication method available" in error_msg:
            raise HTTPException(status_code=401, detail=error_msg)
        elif "Network error" in error_msg or "connecting to Navidrome" in error_msg:
            raise HTTPException(status_code=503, detail=f"Cannot connect to Navidrome server: {error_msg}")
        else:
            raise HTTPException(status_code=500, detail=f"Failed to fetch genres: {error_msg}")

@app.get("/api/songs")
async def search_songs(q: str = Query(..., min_length=1), library_id: List[str] = Query(None)):
    """Search for songs in Navidrome (used as a Radio seed)"""
    try:
        client = get_navidrome_client()
        songs = await client.search_songs(q, count=25, library_ids=library_id)
        return songs
    except Exception as e:
        error_msg = str(e)
        if "Invalid username or password" in error_msg or "No authentication method available" in error_msg:
            raise HTTPException(status_code=401, detail=error_msg)
        elif "Network error" in error_msg or "connecting to Navidrome" in error_msg:
            raise HTTPException(status_code=503, detail=f"Cannot connect to Navidrome server: {error_msg}")
        else:
            raise HTTPException(status_code=500, detail=f"Failed to search songs: {error_msg}")

@app.get("/api/music-folders")
async def get_music_folders():
    """Get list of music folders/libraries from Navidrome"""
    try:
        client = get_navidrome_client()
        folders = await client.get_music_folders()
        return folders
    except Exception as e:
        error_msg = str(e)
        # Check if it's an authentication error and return appropriate status code
        if "Invalid username or password" in error_msg or "No authentication method available" in error_msg:
            raise HTTPException(status_code=401, detail=error_msg)
        elif "Network error" in error_msg or "connecting to Navidrome" in error_msg:
            raise HTTPException(status_code=503, detail=f"Cannot connect to Navidrome server: {error_msg}")
        else:
            raise HTTPException(status_code=500, detail=f"Failed to fetch music folders: {error_msg}")


# SYSTEM CHECK FEATURE - START
@app.get("/api/health-check")
async def get_health_check():
    """Get system health check results"""
    global system_check_passed, system_check_results
    
    try:
        # Run fresh health checks
        health_service = HealthCheckService()
        fresh_results = await health_service.run_checks()
        
        # Update app state with fresh results
        system_check_passed = fresh_results.get("all_passed", False)
        system_check_results = fresh_results
        
        # Log the result
        if system_check_passed:
            scheduler_logger.info("✅ System health checks passed via API")
        else:
            scheduler_logger.warning("⚠️ System health checks failed via API")
        
        return fresh_results
        
    except Exception as e:
        scheduler_logger.error(f"❌ Failed to run health checks via API: {e}")
        error_results = {
            "all_passed": False,
            "checks": [{
                "name": "System Check Service",
                "status": "error",
                "message": f"Failed to run health checks: {str(e)}",
                "suggestion": "Check application logs and restart the service"
            }]
        }
        return error_results
# SYSTEM CHECK FEATURE - END


@app.post("/api/create_playlist", response_model=Playlist)
async def create_playlist(
    request: CreatePlaylistRequest,
    db: DatabaseManager = Depends(get_db)
):
    """Create an AI-curated 'This Is' playlist for a single artist"""
    try:
        # Get clients
        nav_client = get_navidrome_client()
        ai_client_instance = get_ai_client()
        
        # Get artist info
        all_artists = await nav_client.get_artists()
        selected_artists = [a for a in all_artists if a["id"] in request.artist_ids]
        
        if not selected_artists:
            raise HTTPException(status_code=404, detail="Artists not found")
        
        # Limit to single artist only - use first artist from the request
        if request.artist_ids:
            first_artist_id = request.artist_ids[0]
            selected_artists = [a for a in all_artists if a["id"] == first_artist_id]
            artist_names = [a["name"] for a in selected_artists]
        else:
            raise HTTPException(status_code=400, detail="At least one artist must be selected")

        # Generate playlist name if not provided - for single artist
        playlist_name = request.playlist_name or f"This Is: {artist_names[0]}"
        
        # Get tracks for only the first artist
        all_tracks = []
        tracks = await nav_client.get_tracks_by_artist(first_artist_id, request.library_ids)
        if tracks:
            all_tracks.extend(tracks)
        
        if not all_tracks:
            raise HTTPException(status_code=404, detail="No tracks found for the selected artists")
        
        # NEW: Apply smart filtering for "This Is" playlists to optimize LLM payload
        library_stats = await nav_client.get_library_stats()
        await apply_loved_signal(all_tracks)

        filtered_tracks, filter_metadata = filter_tracks_for_this_is_playlist(
            source_tracks=all_tracks,
            target_playlist_size=request.playlist_length,
            library_stats=library_stats
        )
        
        # Log filtering results for analytics/debugging
        if filter_metadata['filtered']:
            scheduler_logger.info(f"🎯 Smart filtering applied: {filter_metadata['source_count']} → {filter_metadata['sent_count']} tracks (multiplier: {filter_metadata['threshold_multiplier']}x)")
            scheduler_logger.info(f"📊 Score range: {filter_metadata['score_range']['highest']:.1f} - {filter_metadata['score_range']['lowest']:.1f} (cutoff: {filter_metadata['score_range']['cutoff']:.1f})")
        else:
            scheduler_logger.info(f"✅ No filtering needed: {filter_metadata['source_count']} tracks below threshold")
        
        # Use filtered tracks for LLM processing
        tracks_for_llm = filtered_tracks
        
        # Use AI to curate the playlist (always include reasoning for new recipe format)
        curation_result = await ai_client_instance.curate_this_is(
            artist_name=', '.join(artist_names),
            tracks_json=tracks_for_llm,
            num_tracks=request.playlist_length,
            include_reasoning=True
        )
        
        # Handle both old and new return formats
        if isinstance(curation_result, tuple):
            curated_track_ids, reasoning = curation_result
        else:
            curated_track_ids = curation_result
            reasoning = ""

        # Check for validation failures or empty results
        if not curated_track_ids:
            if reasoning and "Playlist generation failed" in reasoning:
                # This is a validation failure - don't create playlist
                scheduler_logger.error(f"❌ Playlist creation aborted: {reasoning}")
                raise HTTPException(status_code=400, detail=f"Playlist generation failed: {reasoning}")
            else:
                # This is an empty result without explanation
                scheduler_logger.error(f"❌ AI curation returned no tracks for {', '.join(artist_names)}")
                raise HTTPException(status_code=500, detail="AI curation failed to return any tracks")

        # Log the AI reasoning for debugging (truncated)
        if reasoning:
            reasoning_preview = reasoning[:200] + "..." if len(reasoning) > 200 else reasoning
            scheduler_logger.info(f"🎵 AI curation applied for {', '.join(artist_names)} (reasoning length: {len(reasoning)} chars): {reasoning_preview}")
        else:
            scheduler_logger.info(f"⚠️ No AI reasoning provided for {', '.join(artist_names)}")

        # Create playlist in Navidrome with AI reasoning as comment
        comment_to_use = reasoning if reasoning else None
        comment_preview = comment_to_use[:200] + "..." if comment_to_use and len(comment_to_use) > 200 else comment_to_use
        scheduler_logger.info(f"💬 Creating playlist with comment (length: {len(comment_to_use) if comment_to_use else 0}): {comment_preview}")

        navidrome_playlist_id = await nav_client.create_playlist(
            name=playlist_name,
            track_ids=curated_track_ids,
            comment=comment_to_use
        )
        
        # Get track titles for database storage - PRESERVE AI CURATION ORDER
        # Note: Use all_tracks for mapping since AI might reference tracks from full set
        track_summaries = summarise_tracks(all_tracks, order=curated_track_ids)
        
        
        # Store playlist in local database (using the first artist_id for now)
        playlist = await db.create_playlist(
            artist_id=request.artist_ids[0],
            playlist_name=playlist_name,
            songs=track_summaries,
            reasoning=reasoning,
            navidrome_playlist_id=navidrome_playlist_id,
            playlist_length=request.playlist_length,
            library_ids=request.library_ids
        )
        
        # Handle scheduling if not "none" or "never"
        if request.refresh_frequency not in ["none", "never"]:
            next_refresh = calculate_next_refresh(request.refresh_frequency)
            
            # Store the scheduled playlist
            await db.create_scheduled_playlist(
                playlist_type="this_is",
                navidrome_playlist_id=navidrome_playlist_id,
                refresh_frequency=request.refresh_frequency,
                next_refresh=next_refresh
            )
            
            # Schedule the refresh job
            schedule_playlist_refresh()
            scheduler_logger.info(f"📅 Scheduled {request.refresh_frequency} refresh for This Is playlist: {playlist_name}")
        
        # Add Navidrome playlist ID to response
        playlist_dict = playlist.dict() if hasattr(playlist, 'dict') else playlist.__dict__
        playlist_dict["navidrome_playlist_id"] = navidrome_playlist_id
        playlist_dict["refresh_frequency"] = request.refresh_frequency
        
        if request.refresh_frequency != "none":
            playlist_dict["next_refresh"] = calculate_next_refresh(request.refresh_frequency).isoformat()
        
        return playlist_dict
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create playlist: {str(e)}")

@app.post("/api/create_playlist_with_reasoning")
async def create_playlist_with_reasoning(
    request: CreatePlaylistRequest,
    db: DatabaseManager = Depends(get_db)
):
    """Create an AI-curated 'This Is' playlist with AI reasoning explanation"""
    try:
        # Get clients
        nav_client = get_navidrome_client()
        ai_client_instance = get_ai_client()
        
        # Get artist info - use first artist from the array
        artists = await nav_client.get_artists()
        if not request.artist_ids or len(request.artist_ids) == 0:
            raise HTTPException(status_code=400, detail="At least one artist must be selected")
        first_artist_id = request.artist_ids[0]
        artist = next((a for a in artists if a["id"] == first_artist_id), None)
        
        if not artist:
            raise HTTPException(status_code=404, detail="Artist not found")
        
        artist_name = artist["name"]
        
        # Generate playlist name if not provided
        playlist_name = getattr(request, 'playlist_name', None) or f"This Is: {artist_name}"
        
        # Get tracks for the artist
        tracks = await nav_client.get_tracks_by_artist(first_artist_id)
        
        if not tracks:
            raise HTTPException(status_code=404, detail="No tracks found for this artist")
        
        # Use AI to curate the playlist WITH reasoning
        curated_track_ids, reasoning = await ai_client_instance.curate_this_is(
            artist_name=artist_name,
            tracks_json=tracks,
            num_tracks=20,
            include_reasoning=True
        )

        # Create playlist in Navidrome with AI reasoning as comment
        navidrome_playlist_id = await nav_client.create_playlist(
            name=playlist_name,
            track_ids=curated_track_ids,
            comment=reasoning if reasoning else None
        )
        
        # Get track titles for database storage
        track_summaries = summarise_tracks(tracks, order=curated_track_ids)
        
        # Store playlist in local database
        playlist = await db.create_playlist(
            artist_id=first_artist_id,
            playlist_name=playlist_name,
            songs=track_summaries,
            navidrome_playlist_id=navidrome_playlist_id
        )
        
        # Add Navidrome playlist ID and AI reasoning to response
        playlist_dict = playlist.dict() if hasattr(playlist, 'dict') else playlist.__dict__
        playlist_dict["navidrome_playlist_id"] = navidrome_playlist_id
        playlist_dict["ai_reasoning"] = reasoning
        
        return playlist_dict
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create playlist with reasoning: {str(e)}")

@app.post("/api/create_genre_playlist", response_model=Playlist)
async def create_genre_playlist(
    request: CreateGenrePlaylistRequest,
    db: DatabaseManager = Depends(get_db)
):
    """Create an AI-curated 'Genre Mix' playlist for a specific genre"""
    try:
        # Get clients
        nav_client = get_navidrome_client()
        ai_client_instance = get_ai_client()

        # Generate playlist name if not provided
        playlist_name = request.playlist_name or f"Genre Mix: {request.genre}"

        # Get tracks for the genre
        all_tracks = await nav_client.get_tracks_by_genre(request.genre, request.library_ids)
        scheduler_logger.info(f"🎵 Found {len(all_tracks)} total tracks for genre '{request.genre}'")

        if not all_tracks:
            raise HTTPException(status_code=404, detail=f"No tracks found for genre: {request.genre}")

        # NEW: Apply smart filtering for "Genre Mix" playlists to optimize LLM payload
        library_stats = await nav_client.get_library_stats()
        await apply_loved_signal(all_tracks)

        filtered_tracks, filter_metadata = filter_tracks_for_this_is_playlist(
            source_tracks=all_tracks,
            target_playlist_size=request.playlist_length,
            library_stats=library_stats
        )

        # Log filtering results for analytics/debugging
        if filter_metadata['filtered']:
            scheduler_logger.info(f"🎯 Smart filtering applied: {filter_metadata['source_count']} → {filter_metadata['sent_count']} tracks (multiplier: {filter_metadata['threshold_multiplier']}x)")
            scheduler_logger.info(f"📊 Score range: {filter_metadata['score_range']['highest']:.1f} - {filter_metadata['score_range']['lowest']:.1f} (cutoff: {filter_metadata['score_range']['cutoff']:.1f})")
        else:
            scheduler_logger.info(f"✅ No filtering needed: {filter_metadata['source_count']} tracks below threshold")

        # Use filtered tracks for LLM processing
        tracks_for_llm = filtered_tracks

        # Use AI to curate the playlist (always include reasoning for new recipe format)
        curation_result = await ai_client_instance.curate_genre_mix(
            genre=request.genre,
            tracks_json=tracks_for_llm,
            num_tracks=request.playlist_length,
            include_reasoning=True
        )

        # Handle both old and new return formats
        if isinstance(curation_result, tuple):
            curated_track_ids, reasoning = curation_result
        else:
            curated_track_ids = curation_result
            reasoning = ""

        # Check for validation failures or empty results
        if not curated_track_ids:
            if reasoning and "Playlist generation failed" in reasoning:
                # This is a validation failure - don't create playlist
                scheduler_logger.error(f"❌ Playlist creation aborted: {reasoning}")
                raise HTTPException(status_code=400, detail=f"Playlist generation failed: {reasoning}")
            else:
                # This is an empty result without explanation
                scheduler_logger.error(f"❌ AI curation returned no tracks for {request.genre}")
                raise HTTPException(status_code=500, detail="AI curation failed to return any tracks")

        # Log the AI reasoning for debugging (truncated)
        if reasoning:
            reasoning_preview = reasoning[:200] + "..." if len(reasoning) > 200 else reasoning
            scheduler_logger.info(f"🎵 AI curation applied for {request.genre} (reasoning length: {len(reasoning)} chars): {reasoning_preview}")
        else:
            scheduler_logger.info(f"⚠️ No AI reasoning provided for {request.genre}")

        # Create playlist in Navidrome with AI reasoning as comment
        comment_to_use = reasoning if reasoning else None
        comment_preview = comment_to_use[:200] + "..." if comment_to_use and len(comment_to_use) > 200 else comment_to_use
        scheduler_logger.info(f"💬 Creating playlist with comment (length: {len(comment_to_use) if comment_to_use else 0}): {comment_preview}")

        navidrome_playlist_id = await nav_client.create_playlist(
            name=playlist_name,
            track_ids=curated_track_ids,
            comment=comment_to_use
        )

        # Get track titles for database storage
        track_summaries = summarise_tracks(all_tracks, order=curated_track_ids)


        # Store playlist in local database (using genre as identifier)
        playlist = await db.create_playlist(
            artist_id=request.genre,  # Using genre as artist_id for now
            playlist_name=playlist_name,
            songs=track_summaries,
            reasoning=reasoning,
            navidrome_playlist_id=navidrome_playlist_id,
            playlist_length=request.playlist_length,
            library_ids=request.library_ids
        )

        # Handle scheduling if not "none" or "never"
        if request.refresh_frequency not in ["none", "never"]:
            next_refresh = calculate_next_refresh(request.refresh_frequency)

            # Store the scheduled playlist
            await db.create_scheduled_playlist(
                playlist_type="genre_mix",
                navidrome_playlist_id=navidrome_playlist_id,
                refresh_frequency=request.refresh_frequency,
                next_refresh=next_refresh
            )

        return playlist

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create genre playlist: {str(e)}")

@app.post("/api/create_radio_playlist")
async def create_radio_playlist(
    request: CreateRadioPlaylistRequest,
    db: DatabaseManager = Depends(get_db)
):
    """Create an AI-curated 'Radio' playlist seeded from an artist or song.

    Pools similar-style tracks from the library (seed artist + similar artists),
    lets the AI curate by style/popularity/release date, and returns AI-suggested
    albums by fitting artists that are NOT currently in the library.
    """
    try:
        nav_client = get_navidrome_client()
        ai_client_instance = get_ai_client()

        if request.seed_type not in ("artist", "song"):
            raise HTTPException(status_code=400, detail="seed_type must be 'artist' or 'song'")

        # Resolve the seed and gather candidate tracks
        processor = RadioProcessor(nav_client)
        try:
            seed = await processor.resolve_seed(request.seed_type, request.seed_id, request.library_ids)
        except Exception as e:
            if "not found" in str(e).lower():
                raise HTTPException(status_code=404, detail=f"Radio seed not found: {e}")
            raise

        candidate_tracks = await processor.gather_candidate_tracks(seed, request.library_ids)
        if not candidate_tracks:
            raise HTTPException(status_code=404, detail="No candidate tracks found for this radio seed")

        # Smart-filter to keep the LLM payload manageable (reuses engagement scoring)
        library_stats = await nav_client.get_library_stats()
        await apply_loved_signal(candidate_tracks)
        filtered_tracks, filter_metadata = filter_tracks_for_this_is_playlist(
            source_tracks=candidate_tracks,
            target_playlist_size=request.playlist_length,
            library_stats=library_stats
        )
        if filter_metadata['filtered']:
            scheduler_logger.info(f"🎯 Radio smart filtering: {filter_metadata['source_count']} → {filter_metadata['sent_count']} tracks")

        # AI curation (returns tracks, reasoning, and album suggestions)
        curated_track_ids, reasoning, album_suggestions = await ai_client_instance.curate_radio(
            seed_name=seed["name"],
            tracks_json=filtered_tracks,
            num_tracks=request.playlist_length,
            include_reasoning=True
        )

        if not curated_track_ids:
            scheduler_logger.error(f"❌ Radio curation returned no tracks for {seed['name']}")
            raise HTTPException(status_code=500, detail="AI curation failed to return any tracks")

        playlist_name = request.playlist_name or f"{seed['name'] if seed['type'] == 'artist' else seed['song_title']} Radio"

        # Map curated IDs back to the full tracks, preserving curation order
        track_by_id = {track["id"]: track for track in candidate_tracks}
        curated_tracks = [track_by_id[tid] for tid in curated_track_ids if tid in track_by_id]

        # Hold the seed artist to its share of the station, then guarantee the
        # station still opens with the seed (the cap keeps its earliest tracks)
        curated_tracks, seed_cap = cap_seed_artist(
            curated_tracks, seed,
            num_tracks=request.playlist_length,
            candidate_pool=candidate_tracks
        )
        if seed_cap["dropped_for_seed_cap"]:
            scheduler_logger.info(
                f"📻 Radio: capped seed artist '{seed.get('artist_name')}' at "
                f"{seed_cap['seed_artist_limit']} track(s), dropped "
                f"{seed_cap['dropped_for_seed_cap']}, backfilled {seed_cap['backfilled']}"
            )

        curated_tracks = promote_seed_first(curated_tracks, seed, candidate_tracks)
        curated_track_ids = [track["id"] for track in curated_tracks]
        track_summaries = summarise_tracks(curated_tracks)

        # Create the playlist in Navidrome (reasoning stored as comment)
        comment_to_use = reasoning if reasoning else None
        navidrome_playlist_id = await nav_client.create_playlist(
            name=playlist_name,
            track_ids=curated_track_ids,
            comment=comment_to_use
        )

        # Report the gap when a thin library couldn't fill the requested length
        shortfall = build_shortfall(
            requested=request.playlist_length,
            delivered=len(track_summaries),
            candidate_pool_size=len(candidate_tracks),
            distinct_artists=count_distinct_artists(curated_tracks),
            warnings=processor.pool_warnings
        )
        if shortfall["is_short"]:
            scheduler_logger.info(
                f"📻 Radio: '{playlist_name}' came up {shortfall['missing']} track(s) short "
                f"({len(track_summaries)}/{request.playlist_length}) from a pool of {len(candidate_tracks)}"
            )

        # Point each "not in your library" album at Lidarr's add-new search
        album_suggestions = [
            {**suggestion, "lidarr_url": lidarr_add_url(suggestion.get("artist"), suggestion.get("album"))}
            for suggestion in (album_suggestions or [])
        ]

        # Store the seed in artist_id so scheduled refreshes can regenerate the station
        stored_seed_id = f"radio:{seed['type']}:{seed['id']}"
        playlist = await db.create_playlist(
            artist_id=stored_seed_id,
            playlist_name=playlist_name,
            songs=track_summaries,
            reasoning=reasoning,
            navidrome_playlist_id=navidrome_playlist_id,
            playlist_length=request.playlist_length,
            library_ids=request.library_ids,
            album_suggestions=album_suggestions,
            build_info=shortfall
        )

        # Handle scheduling if requested
        if request.refresh_frequency not in ["none", "never"]:
            next_refresh = calculate_next_refresh(request.refresh_frequency)
            await db.create_scheduled_playlist(
                playlist_type="radio",
                navidrome_playlist_id=navidrome_playlist_id,
                refresh_frequency=request.refresh_frequency,
                next_refresh=next_refresh
            )
            schedule_playlist_refresh()
            scheduler_logger.info(f"📅 Scheduled {request.refresh_frequency} refresh for Radio playlist: {playlist_name}")

        playlist_dict = playlist.dict() if hasattr(playlist, 'dict') else playlist.__dict__
        playlist_dict["navidrome_playlist_id"] = navidrome_playlist_id
        playlist_dict["refresh_frequency"] = request.refresh_frequency
        playlist_dict["reasoning"] = reasoning
        playlist_dict["album_suggestions"] = album_suggestions
        playlist_dict["seed_name"] = seed["name"]
        playlist_dict["track_count"] = len(track_summaries)
        playlist_dict["shortfall"] = shortfall
        return playlist_dict

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        if "Invalid username or password" in error_msg or "No authentication method available" in error_msg:
            raise HTTPException(status_code=401, detail=error_msg)
        elif "Network error" in error_msg or "connecting to Navidrome" in error_msg:
            raise HTTPException(status_code=503, detail=f"Cannot connect to Navidrome server: {error_msg}")
        raise HTTPException(status_code=500, detail=f"Failed to create radio playlist: {error_msg}")

@app.get("/api/rediscover-weekly", response_model=RediscoverWeeklyResponse)
async def get_rediscover_weekly():
    """Generate Re-Discover Weekly playlist based on listening history"""
    try:
        # Get Navidrome client
        nav_client = get_navidrome_client()
        
        # Create RediscoverWeekly instance
        rediscover = RediscoverWeekly(nav_client)
        
        # Generate the playlist with AI curation
        tracks = await rediscover.generate_rediscover_weekly(use_ai=True)
        
        # Extract AI curation info for response
        ai_curated = tracks[0].get("ai_curated", False) if tracks else False
        message = f"Generated Re-Discover Weekly with {len(tracks)} tracks"
        if ai_curated:
            message += " (AI curated)"
        else:
            message += " (algorithmic selection)"
        
        return RediscoverWeeklyResponse(
            tracks=tracks,
            total_tracks=len(tracks),
            message=message
        )
        
    except Exception as e:
        error_msg = str(e)
        if "No listening history found" in error_msg:
            raise HTTPException(status_code=404, detail="No listening history found. Make sure you've played some music in Navidrome.")
        elif "No tracks found for re-discovery" in error_msg:
            raise HTTPException(status_code=404, detail="No tracks found for re-discovery. Try listening to more music first.")
        elif "Invalid username or password" in error_msg or "No authentication method available" in error_msg:
            raise HTTPException(status_code=401, detail=error_msg)
        elif "Network error" in error_msg or "connecting to Navidrome" in error_msg:
            raise HTTPException(status_code=503, detail=f"Cannot connect to Navidrome server: {error_msg}")
        else:
            raise HTTPException(status_code=500, detail=f"Failed to generate Re-Discover Weekly: {error_msg}")

@app.get("/api/rediscover-weekly-v2", response_model=RediscoverWeeklyV2Response)
async def get_rediscover_weekly_v2(library_ids: Optional[List[str]] = Query(None), db: DatabaseManager = Depends(get_db)):
    """Generate Re-Discover Weekly v2.0 playlist using temporal analysis and two-phase AI"""
    try:
        # Get clients
        nav_client = get_navidrome_client()
        ai_client = get_ai_client()

        # Get user and server IDs
        user_id = await db.get_or_create_user_id()
        server_id = nav_client.base_url or "unknown_server"  # Use base URL as server identifier

        # Create ReDiscoverV2Processor instance
        processor = ReDiscoverV2Processor(nav_client, ai_client, db)

        # Generate the playlist
        result = await processor.generate_playlist(user_id, server_id, library_ids)

        return RediscoverWeeklyV2Response(**result)

    except Exception as e:
        error_msg = str(e)
        if "Insufficient listening history" in error_msg:
            raise HTTPException(status_code=404, detail="Insufficient listening history. Star favorites and listen regularly. Check back in 2-3 weeks!")
        elif "Invalid username or password" in error_msg or "No authentication method available" in error_msg:
            raise HTTPException(status_code=401, detail=error_msg)
        elif "Network error" in error_msg or "connecting to Navidrome" in error_msg:
            raise HTTPException(status_code=503, detail=f"Cannot connect to Navidrome server: {error_msg}")
        else:
            raise HTTPException(status_code=500, detail=f"Failed to generate Re-Discover Weekly v2.0: {error_msg}")

@app.post("/api/create-rediscover-playlist-v2")
async def create_rediscover_playlist_v2(
    request: CreateRediscoverPlaylistRequest,
    db: DatabaseManager = Depends(get_db)
):
    """Create a Re-Discover Weekly v2.0 playlist in Navidrome"""
    try:
        scheduler_logger.info(f"🎵 Starting Re-Discover v2.0 playlist creation with length {request.playlist_length}, library_ids: {request.library_ids}")

        # Get clients
        nav_client = get_navidrome_client()
        ai_client = get_ai_client()

        # Get user and server IDs
        user_id = await db.get_or_create_user_id()
        server_id = nav_client.base_url or "unknown_server"

        # Create ReDiscoverV2Processor instance
        processor = ReDiscoverV2Processor(nav_client, ai_client, db)

        # Generate the playlist
        playlist_data = await processor.generate_playlist(user_id, server_id, request.library_ids)
        tracks = playlist_data.get("tracks", [])

        if not tracks:
            scheduler_logger.error("❌ No tracks generated for Re-Discover Weekly v2.0")
            raise HTTPException(status_code=404, detail="No tracks found for Re-Discover Weekly v2.0")

        scheduler_logger.info(f"✅ Generated {len(tracks)} tracks for Re-Discover Weekly v2.0")

        # Extract AI reasoning if available
        ai_reasoning = playlist_data.get("reasoning", "")
        ai_curated = any(track.get("ai_curated", False) for track in tracks)

        # If AI curated, get reasoning from the tracks instead of Phase 1
        if ai_curated:
            track_reasoning = next((track.get("ai_reasoning", "") for track in tracks if track.get("ai_curated", False) and track.get("ai_reasoning")), "")
            if track_reasoning:
                ai_reasoning = track_reasoning

        scheduler_logger.info(f"🎵 AI curated: {ai_curated}, reasoning length: {len(ai_reasoning)}")

        # Log the AI reasoning for debugging (truncated)
        if ai_reasoning and ai_curated:
            reasoning_preview = ai_reasoning[:200] + "..." if len(ai_reasoning) > 200 else ai_reasoning
            scheduler_logger.info(f"🎵 AI curation applied for Re-Discover Weekly v2.0 (reasoning length: {len(ai_reasoning)} chars): {reasoning_preview}")
        else:
            scheduler_logger.info(f"⚠️ Re-Discover Weekly v2.0 used fallback strategy")

        # Create playlist name based on refresh frequency
        frequency_names = {
            "daily": "Re-Discover Daily ✨",
            "weekly": "Re-Discover Weekly ✨",
            "monthly": "Re-Discover Monthly ✨",
            "never": "Re-Discover ✨"
        }
        playlist_name = frequency_names.get(request.refresh_frequency, "Re-Discover Weekly ✨")
        if playlist_data.get("is_fallback"):
            playlist_name += " (Fallback)"
        scheduler_logger.info(f"📝 Creating playlist: {playlist_name}")

        # Extract track IDs
        track_ids = [track["id"] for track in tracks]
        scheduler_logger.info(f"🎵 Track IDs: {track_ids[:5]}... (total: {len(track_ids)})")

        # Create playlist in Navidrome with reasoning as comment
        comment_to_use = ai_reasoning if ai_reasoning else f"Theme: {playlist_data.get('theme', 'Mixed')}"
        comment_preview = comment_to_use[:200] + "..." if len(comment_to_use) > 200 else comment_to_use
        scheduler_logger.info(f"💬 Creating Re-Discover v2.0 playlist with comment (length: {len(comment_to_use)}): {comment_preview}")

        scheduler_logger.info("🎵 Calling nav_client.create_playlist...")
        navidrome_playlist_id = await nav_client.create_playlist(
            name=playlist_name,
            track_ids=track_ids,
            comment=comment_to_use
        )
        scheduler_logger.info(f"✅ Navidrome playlist created: {navidrome_playlist_id}")

        # Get track titles for database storage
        track_summaries = summarise_tracks(tracks)
        scheduler_logger.info(f"📊 Storing {len(track_summaries)} tracks in database")

        # Store playlist in local database (using a synthetic artist_id for rediscover playlists)
        playlist_record = await db.create_playlist(
            artist_id="rediscover_v2",
            playlist_name=playlist_name,
            songs=track_summaries,
            reasoning=ai_reasoning,
            navidrome_playlist_id=navidrome_playlist_id,
            # The length the user ASKED for, not what we managed to deliver.
            # Storing len(tracks) meant an underfilled playlist permanently
            # shrank its own target: rebuild a short 25 and it would only ever
            # ask for what it got last time.
            playlist_length=request.playlist_length,
            library_ids=request.library_ids
        )
        scheduler_logger.info(f"💾 Database playlist created: {playlist_record}")

        # Set up scheduling if requested
        if request.refresh_frequency != "never":
            scheduler_logger.info(f"⏰ Setting up {request.refresh_frequency} refresh schedule")
            scheduled_playlist = await db.create_scheduled_playlist(
                playlist_type="rediscover_weekly_v2",
                navidrome_playlist_id=navidrome_playlist_id,
                refresh_frequency=request.refresh_frequency,
                next_refresh=calculate_next_refresh(request.refresh_frequency)
            )
            scheduler_logger.info(f"✅ Scheduled playlist created: {scheduled_playlist}")
        else:
            scheduler_logger.info("⏰ No scheduling requested (refresh_frequency='never')")

        return {
            "message": f"Re-Discover Weekly v2.0 playlist created successfully with {len(tracks)} tracks",
            "playlist_id": navidrome_playlist_id,
            "track_count": len(tracks),
            "theme": playlist_data.get("theme", "Mixed"),
            "mode": playlist_data.get("mode", "Unknown"),
            "is_fallback": playlist_data.get("is_fallback", False)
        }

    except HTTPException:
        raise
    except Exception as e:
        scheduler_logger.error(f"❌ Failed to create Re-Discover Weekly v2.0 playlist: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create Re-Discover Weekly v2.0 playlist: {str(e)}")

@app.post("/api/create-rediscover-playlist")
async def create_rediscover_playlist(
    request: CreateRediscoverPlaylistRequest,
    db: DatabaseManager = Depends(get_db)
):
    """Create a Re-Discover Weekly playlist in Navidrome"""
    try:
        scheduler_logger.info(f"🎵 Starting Re-Discover playlist creation with length {request.playlist_length}, library_ids: {request.library_ids}")

        # Get Navidrome client
        nav_client = get_navidrome_client()

        # Create RediscoverWeekly instance
        rediscover = RediscoverWeekly(nav_client)

        # Generate the playlist tracks with user-specified length and AI curation
        scheduler_logger.info("🎵 Generating rediscover tracks...")
        tracks = await rediscover.generate_rediscover_weekly(max_tracks=request.playlist_length, use_ai=True, library_id=request.library_ids[0] if request.library_ids else "", variety_context="")
        scheduler_logger.info(f"🎵 Generated {len(tracks) if tracks else 0} tracks")
        
        if not tracks:
            scheduler_logger.error("❌ No tracks generated for Re-Discover Weekly")
            raise HTTPException(status_code=404, detail="No tracks found for Re-Discover Weekly")

        scheduler_logger.info(f"✅ Generated {len(tracks)} tracks for Re-Discover Weekly")

        # Extract AI reasoning if available
        ai_reasoning = ""
        ai_curated = False
        if tracks:
            first_track = tracks[0]
            ai_reasoning = first_track.get("ai_reasoning", "")
            ai_curated = first_track.get("ai_curated", False)
            scheduler_logger.info(f"🎵 AI curated: {ai_curated}, reasoning length: {len(ai_reasoning)}")
        
        # Log the AI reasoning for debugging (truncated)
        if ai_reasoning and ai_curated:
            reasoning_preview = ai_reasoning[:200] + "..." if len(ai_reasoning) > 200 else ai_reasoning
            scheduler_logger.info(f"🎵 AI curation applied for Re-Discover Weekly (reasoning length: {len(ai_reasoning)} chars): {reasoning_preview}")
        else:
            scheduler_logger.info(f"⚠️ Re-Discover Weekly used algorithmic selection (no AI reasoning)")
        
        # Create playlist name based on frequency
        frequency_names = {
            "daily": "Re-Discover Daily ✨",
            "weekly": "Re-Discover Weekly ✨",
            "monthly": "Re-Discover Monthly ✨",
            "never": "Re-Discover ✨"
        }
        playlist_name = frequency_names.get(request.refresh_frequency, "Re-Discover Weekly ✨")
        scheduler_logger.info(f"📝 Creating playlist: {playlist_name}")

        # Extract track IDs
        track_ids = [track["id"] for track in tracks]
        scheduler_logger.info(f"🎵 Track IDs: {track_ids[:5]}... (total: {len(track_ids)})")

        # Create playlist in Navidrome with AI reasoning as comment if available
        comment_to_use = ai_reasoning if (ai_reasoning and ai_curated) else None
        comment_preview = comment_to_use[:200] + "..." if comment_to_use and len(comment_to_use) > 200 else comment_to_use
        scheduler_logger.info(f"💬 Creating Re-Discover playlist with comment (length: {len(comment_to_use) if comment_to_use else 0}): {comment_preview}")

        scheduler_logger.info("🎵 Calling nav_client.create_playlist...")
        navidrome_playlist_id = await nav_client.create_playlist(
            name=playlist_name,
            track_ids=track_ids,
            comment=comment_to_use
        )
        scheduler_logger.info(f"✅ Navidrome playlist created: {navidrome_playlist_id}")
        
        # Get track titles for database storage
        track_summaries = summarise_tracks(tracks)
        scheduler_logger.info(f"📊 Storing {len(track_summaries)} tracks in database")

        # Store playlist in local database (using a synthetic artist_id for rediscover playlists)
        scheduler_logger.info("💾 Creating playlist in database...")
        playlist = await db.create_playlist(
            artist_id="rediscover",
            playlist_name=playlist_name,
            songs=track_summaries,
            reasoning=ai_reasoning if ai_curated else "Algorithmic selection",
            navidrome_playlist_id=navidrome_playlist_id,
            playlist_length=request.playlist_length
        )
        scheduler_logger.info(f"✅ Database playlist created: {playlist}")
        
        # Handle scheduling if not "never"
        if request.refresh_frequency != "never":
            next_refresh = calculate_next_refresh(request.refresh_frequency)
            
            # Store the scheduled playlist
            await db.create_scheduled_playlist(
                playlist_type="rediscover",
                navidrome_playlist_id=navidrome_playlist_id,
                refresh_frequency=request.refresh_frequency,
                next_refresh=next_refresh
            )
            
            # Schedule the refresh job
            schedule_playlist_refresh()
            scheduler_logger.info(f"📅 Scheduled {request.refresh_frequency} refresh for playlist: {playlist_name}")
        else:
            scheduler_logger.info(f"📅 No scheduling for playlist: {playlist_name} (refresh frequency: never)")
        
        # Add Navidrome playlist ID to response
        playlist_dict = playlist.dict() if hasattr(playlist, 'dict') else playlist.__dict__
        playlist_dict["navidrome_playlist_id"] = navidrome_playlist_id
        playlist_dict["tracks"] = tracks
        playlist_dict["refresh_frequency"] = request.refresh_frequency
        playlist_dict["next_refresh"] = calculate_next_refresh(request.refresh_frequency).isoformat()
        
        return playlist_dict
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create Re-Discover Weekly playlist: {str(e)}")

def calculate_next_refresh(frequency: str) -> datetime:
    """Calculate the next refresh time based on frequency"""
    now = datetime.now()
    if frequency == "daily":
        # Next day at 1:00 AM
        next_day = now + timedelta(days=1)
        return next_day.replace(hour=1, minute=0, second=0, microsecond=0)
    elif frequency == "weekly":
        # Next Monday at 1:00 AM
        days_until_monday = (7 - now.weekday()) % 7
        if days_until_monday == 0 and now.hour >= 1:
            days_until_monday = 7  # If it's Monday after 1 AM, go to next Monday
        next_monday = now + timedelta(days=days_until_monday)
        return next_monday.replace(hour=1, minute=0, second=0, microsecond=0)
    elif frequency == "monthly":
        # 1st of next month at 1:00 AM
        if now.month == 12:
            next_month = now.replace(year=now.year + 1, month=1, day=1, hour=1, minute=0, second=0, microsecond=0)
        else:
            next_month = now.replace(month=now.month + 1, day=1, hour=1, minute=0, second=0, microsecond=0)
        return next_month
    else:
        return now  # Fallback

def schedule_playlist_refresh():
    """Schedule the playlist refresh job to run every 12 hours"""
    if not scheduler.get_job('playlist_refresh'):
        scheduler.add_job(
            refresh_scheduled_playlists,
            'cron',
            hour='1,13',  # Run at 1 AM and 1 PM
            minute=1,     # Run at 1 minute past (1:01 AM and 1:01 PM)
            id='playlist_refresh',
            replace_existing=True
        )
        scheduler_logger.info("🔄 Playlist refresh job scheduled to run every 12 hours (1:01 AM and 1:01 PM)")

async def refresh_scheduled_playlists():
    """Check for and refresh scheduled playlists that are due"""
    try:
        current_time = datetime.now()
        
        # Only log heartbeat in DEBUG mode, always log when tasks are found
        if LOG_LEVEL == "DEBUG":
            scheduler_logger.debug(f"🔄 Scheduler auto-run initiated at {current_time.strftime('%H:%M:%S')}")
        
        if LOG_LEVEL == "DEBUG":
            scheduler_logger.debug("🔍 Checking for playlists due for refresh...")
        else:
            scheduler_logger.info("🔍 Checking for playlists due for refresh...")
        
        # Get database path from environment variable with smart defaults
        # Docker: /app/data/magiclists.db (set in docker-compose.yml)
        # Standalone: ./magiclists.db (current directory)
        default_path = "/app/data/magiclists.db" if os.path.exists("/app/data") else "./magiclists.db"
        db_path = os.getenv("DATABASE_PATH", default_path)
        db = DatabaseManager(db_path)
        current_time = datetime.now()
        
        # Get playlists due for refresh (including 7-day catch-up window)
        scheduled_playlists = await db.get_scheduled_playlists_due(current_time, grace_hours=168)
        
        if not scheduled_playlists:
            if LOG_LEVEL == "DEBUG":
                scheduler_logger.debug("✅ No playlists due for refresh at this time")
            return
        
        # Group by navidrome_playlist_id to prevent duplicate processing
        # Only process the most recent overdue refresh for each playlist
        unique_playlists = {}
        for playlist in scheduled_playlists:
            playlist_id = playlist.navidrome_playlist_id
            if playlist_id not in unique_playlists:
                unique_playlists[playlist_id] = playlist
            else:
                # Keep the more recent one (closer to current time)
                existing = datetime.fromisoformat(unique_playlists[playlist_id].next_refresh)
                current = datetime.fromisoformat(playlist.next_refresh)
                if current > existing:
                    unique_playlists[playlist_id] = playlist
        
        final_playlists = list(unique_playlists.values())
        
        scheduler_logger.info(f"📋 Found {len(final_playlists)} playlist(s) due for refresh (deduplicated from {len(scheduled_playlists)} total)")
        
        for scheduled_playlist in final_playlists:
            # Check if this is a catch-up refresh
            scheduled_time = datetime.fromisoformat(scheduled_playlist.next_refresh)
            if scheduled_time < current_time:
                overdue_hours = (current_time - scheduled_time).total_seconds() / 3600
                scheduler_logger.info(f"🕐 Catching up on overdue playlist {scheduled_playlist.navidrome_playlist_id} (missed by {overdue_hours:.1f} hours)")
            
            if scheduled_playlist.playlist_type == "rediscover":
                await refresh_rediscover_playlist(scheduled_playlist, db)
            elif scheduled_playlist.playlist_type == "this_is":
                await refresh_this_is_playlist(scheduled_playlist, db)
            elif scheduled_playlist.playlist_type == "radio":
                await refresh_radio_playlist(scheduled_playlist, db)
                
    except Exception as e:
        scheduler_logger.error(f"❌ Error checking scheduled playlists: {e}")

class ManualRefreshTarget:
    """Stands in for a `scheduled_playlists` row when recreating on demand.

    The refresh functions were written for the scheduler and read their target
    off a scheduled row. A manual recreate has to work for playlists that were
    never scheduled, so it passes one of these instead: same attributes, but a
    None `id` marks that there is no schedule to advance afterwards.
    """

    def __init__(self, navidrome_playlist_id: str, playlist_type: str,
                 refresh_frequency: str = "none", scheduled_id: int = None):
        self.id = scheduled_id
        self.navidrome_playlist_id = navidrome_playlist_id
        self.playlist_type = playlist_type
        self.refresh_frequency = refresh_frequency or "none"
        self.next_refresh = None


async def advance_schedule(scheduled_playlist, db: DatabaseManager, label: str) -> None:
    """Move a playlist's next-refresh time on after a successful rebuild.

    Skipped when there is no schedule behind the rebuild — a manual recreate of
    an unscheduled playlist still counts as a refresh, it just has no next run
    to push forward.
    """
    if getattr(scheduled_playlist, "id", None) is None:
        scheduler_logger.info(f"✅ Recreated {label} (not scheduled — nothing to advance)")
        return

    if scheduled_playlist.refresh_frequency in (None, "none", "never"):
        scheduler_logger.info(f"✅ Recreated {label} (schedule paused)")
        return

    next_refresh = calculate_next_refresh(scheduled_playlist.refresh_frequency)
    await db.update_scheduled_playlist_next_refresh(scheduled_playlist.id, next_refresh)
    scheduler_logger.info(
        f"✅ Successfully refreshed {label}. "
        f"Next refresh: {next_refresh.strftime('%Y-%m-%d %H:%M:%S')}"
    )


async def refresh_rediscover_playlist(scheduled_playlist, db: DatabaseManager, propagate_errors: bool = False):
    """Refresh a specific Re-Discover Weekly playlist"""
    try:
        scheduler_logger.info(f"🔄 Starting refresh for playlist ID: {scheduled_playlist.navidrome_playlist_id} (frequency: {scheduled_playlist.refresh_frequency})")
        
        # Get clients
        nav_client = get_navidrome_client()
        
        # Get original playlist to find user's preferred length
        playlists = await db.get_all_playlists_with_schedule_info()
        original_playlist = next((p for p in playlists if p.get("navidrome_playlist_id") == scheduled_playlist.navidrome_playlist_id), None)
        
        if not original_playlist:
            scheduler_logger.error(f"❌ Could not find original playlist data for {scheduled_playlist.navidrome_playlist_id}")
            return
        
        # Get original playlist length (MUST respect user's choice)
        original_length = original_playlist.get("playlist_length", 20)
        scheduler_logger.info(f"🎯 Using original playlist length: {original_length}")
        
        # Get previous playlist songs for variety context
        previous_songs = song_labels(original_playlist.get("songs", []))[:10]
        variety_instruction = f"REFRESH CHALLENGE: The current playlist opens with these tracks in this order: {', '.join(previous_songs[:5])}. Your goal is to create a FRESH arrangement that tells a different musical story. You may include some of the same excellent tracks if they're rediscovery-worthy, but avoid replicating the same opening sequence or overall flow. Think creatively about re-ordering, substituting, or finding better transitions to ensure a genuinely refreshed listening experience." if previous_songs else ""
        
        # Get AI client for v2.0 processor
        ai_client = get_ai_client()

        # Get user and server IDs for v2.0 processor
        user_id = await db.get_or_create_user_id()
        server_id = nav_client.base_url or "unknown_server"

        # Create ReDiscoverV2Processor instance (improved fallback handling)
        processor = ReDiscoverV2Processor(nav_client, ai_client, db)

        # Prepare library IDs for v2.0 processor
        library_ids = [scheduled_playlist.library_id] if hasattr(scheduled_playlist, 'library_id') and scheduled_playlist.library_id else None

        # Log refresh context for debugging
        scheduler_logger.info(f"🔄 Re-Discover v2.0 refresh context - Previous tracks: {len(previous_songs)}, Library IDs: {library_ids}")

        # Generate new tracks using v2.0 processor with improved fallback handling
        result = await processor.generate_playlist(user_id, server_id, library_ids)

        # Extract tracks from v2.0 result format
        tracks = result.get("tracks", [])

        # Ensure tracks have the expected format for the rest of the refresh logic
        # The v2.0 tracks should already have ai_curated and ai_reasoning fields
        
        # The rediscover.generate_rediscover_weekly() method now uses the new recipe system internally
        
        if tracks:
            scheduler_logger.info(f"🎵 Generated {len(tracks)} new tracks for refresh")
            
            # VALIDATE: Ensure we got the expected number of tracks
            if len(tracks) != original_length:
                scheduler_logger.warning(f"⚠️ Generated {len(tracks)} tracks but user requested {original_length}")
            else:
                scheduler_logger.info(f"✅ Generated exact number of requested tracks: {len(tracks)}")
            
            # Extract AI reasoning if available
            ai_reasoning = ""
            ai_curated = False
            if tracks:
                first_track = tracks[0]
                ai_reasoning = first_track.get("ai_reasoning", "")
                ai_curated = first_track.get("ai_curated", False)
            
            # Log the AI reasoning for scheduled refresh (truncated)
            if ai_reasoning and ai_curated:
                reasoning_preview = ai_reasoning[:200] + "..." if len(ai_reasoning) > 200 else ai_reasoning
                scheduler_logger.info(f"🎵 AI curation applied for scheduled Re-Discover refresh (reasoning length: {len(ai_reasoning)} chars): {reasoning_preview}")
            else:
                scheduler_logger.info(f"⚠️ Scheduled Re-Discover refresh used algorithmic selection")
            
            # Update the existing playlist in Navidrome with reasoning
            track_ids = [track["id"] for track in tracks]
            comment_to_use = ai_reasoning if (ai_reasoning and ai_curated) else "Re-Discover Weekly v2.0 - Automatically refreshed"
            await nav_client.update_playlist(
                playlist_id=scheduled_playlist.navidrome_playlist_id,
                track_ids=track_ids,
                comment=comment_to_use
            )
            
            # Update the local database with new songs and reasoning
            track_summaries = summarise_tracks(tracks)
            reasoning_to_store = ai_reasoning if ai_curated else "Algorithmic selection"
            await db.update_playlist_content(
                navidrome_playlist_id=scheduled_playlist.navidrome_playlist_id,
                songs=track_summaries,
                reasoning=reasoning_to_store
            )
            
            await advance_schedule(
                scheduled_playlist, db,
                f"playlist {scheduled_playlist.navidrome_playlist_id}"
            )
        else:
            scheduler_logger.warning(f"⚠️ No tracks generated for playlist {scheduled_playlist.navidrome_playlist_id}")
            if propagate_errors:
                raise Exception("No tracks were generated for this playlist")

    except Exception as e:
        scheduler_logger.error(f"❌ Error refreshing playlist {scheduled_playlist.navidrome_playlist_id}: {e}")
        if propagate_errors:
            raise

async def refresh_this_is_playlist(scheduled_playlist, db: DatabaseManager, propagate_errors: bool = False):
    """Refresh a specific This Is playlist"""
    try:
        scheduler_logger.info(f"🔄 Starting refresh for This Is playlist ID: {scheduled_playlist.navidrome_playlist_id} (frequency: {scheduled_playlist.refresh_frequency})")
        
        # Get clients
        nav_client = get_navidrome_client()
        ai_client_instance = get_ai_client()
        
        # Find the original playlist to get artist info
        playlists = await db.get_all_playlists_with_schedule_info()
        original_playlist = next((p for p in playlists if p.get("navidrome_playlist_id") == scheduled_playlist.navidrome_playlist_id), None)
        
        if not original_playlist:
            scheduler_logger.error(f"❌ Could not find original playlist data for {scheduled_playlist.navidrome_playlist_id}")
            return
        
        # Get artist IDs from the original playlist (we'll need to store this better in future)
        # For now, we'll use the artist_id field, but this limits us to single artists for refresh
        artist_id = original_playlist["artist_id"]
        
        # Get all artists to find the name
        all_artists = await nav_client.get_artists()
        artist = next((a for a in all_artists if a["id"] == artist_id), None)
        
        if not artist:
            scheduler_logger.error(f"❌ Could not find artist data for ID: {artist_id}")
            return
        
        artist_name = artist["name"]
        
        # FRESH DATA: Re-fetch ALL tracks for the artist (gets latest play counts, dates)
        tracks = await nav_client.get_tracks_by_artist(artist_id)
        
        if tracks:
            scheduler_logger.info(f"🎵 Found {len(tracks)} tracks for artist: {artist_name} (fresh data)")
            
            # ENFORCE original playlist length (MUST respect user's choice)
            original_length = original_playlist.get("playlist_length", 25)
            scheduler_logger.info(f"🎯 ENFORCING original playlist length: {original_length}")
            
            # Check if we have enough tracks
            if len(tracks) < original_length:
                scheduler_logger.warning(f"⚠️ Artist only has {len(tracks)} tracks, but user requested {original_length}. Using all available tracks.")
                original_length = len(tracks)
            
            # Get previous playlist songs for STRONG variety enforcement
            previous_songs = song_labels(original_playlist.get("songs", []))
            variety_instruction = f"REFRESH CONSTRAINT: This is a REFRESH, not a copy. Previous playlist had these tracks: {', '.join(previous_songs[:10])}. Create a completely different track selection and arrangement. Prioritize tracks NOT in the previous list. Tell a fresh musical story. Avoid identical opening sequences." if previous_songs else "Create a fresh, engaging playlist arrangement."
            
            # Prepare tracks with variety instruction - use a more direct approach
            tracks_for_ai = tracks.copy()
            
            # Use AI to curate a FRESH playlist with STRONG variety enforcement
            curation_result = await ai_client_instance.curate_this_is(
                artist_name=artist_name,
                tracks_json=tracks_for_ai,
                num_tracks=original_length,
                include_reasoning=True,
                variety_context=variety_instruction
            )
            
            # Handle both old and new return formats
            if isinstance(curation_result, tuple):
                curated_track_ids, reasoning = curation_result
            else:
                curated_track_ids = curation_result
                reasoning = ""
            
            if curated_track_ids:
                # VALIDATE: Ensure we got the right number of tracks
                if len(curated_track_ids) < original_length and len(tracks) >= original_length:
                    scheduler_logger.warning(f"⚠️ AI returned only {len(curated_track_ids)} tracks but user requested {original_length}. Using fallback to fill gap.")
                    # Fill the gap with remaining tracks
                    used_ids = set(curated_track_ids)
                    remaining_tracks = [t for t in tracks if t["id"] not in used_ids]
                    additional_needed = original_length - len(curated_track_ids)
                    additional_tracks = remaining_tracks[:additional_needed]
                    curated_track_ids.extend([t["id"] for t in additional_tracks])
                
                scheduler_logger.info(f"🎯 Final track count: {len(curated_track_ids)} (requested: {original_length})")
                
                # Update the existing playlist in Navidrome with new reasoning
                await nav_client.update_playlist(
                    playlist_id=scheduled_playlist.navidrome_playlist_id,
                    track_ids=curated_track_ids,
                    comment=reasoning if reasoning else None
                )
                
                # Update the local database with new songs and reasoning
                track_summaries = summarise_tracks(tracks, order=curated_track_ids)
                
                await db.update_playlist_content(
                    navidrome_playlist_id=scheduled_playlist.navidrome_playlist_id,
                    songs=track_summaries,
                    reasoning=reasoning
                )
                
                await advance_schedule(
                    scheduled_playlist, db,
                    f"This Is playlist {scheduled_playlist.navidrome_playlist_id}"
                )
            else:
                scheduler_logger.warning(f"⚠️ No curated tracks generated for This Is playlist {scheduled_playlist.navidrome_playlist_id}")
                if propagate_errors:
                    raise Exception("The AI returned no tracks for this playlist")
        else:
            scheduler_logger.warning(f"⚠️ No tracks found for artist {artist_name} in playlist {scheduled_playlist.navidrome_playlist_id}")
            if propagate_errors:
                raise Exception(f"No tracks found for artist {artist_name}")

    except Exception as e:
        scheduler_logger.error(f"❌ Error refreshing This Is playlist {scheduled_playlist.navidrome_playlist_id}: {e}")
        if propagate_errors:
            raise

async def refresh_radio_playlist(scheduled_playlist, db: DatabaseManager, propagate_errors: bool = False):
    """Refresh a specific Radio playlist by regenerating the station from its seed"""
    try:
        scheduler_logger.info(f"🔄 Starting refresh for Radio playlist ID: {scheduled_playlist.navidrome_playlist_id} (frequency: {scheduled_playlist.refresh_frequency})")

        nav_client = get_navidrome_client()
        ai_client_instance = get_ai_client()

        # Find the original playlist to recover the seed and length
        playlists = await db.get_all_playlists_with_schedule_info()
        original_playlist = next((p for p in playlists if p.get("navidrome_playlist_id") == scheduled_playlist.navidrome_playlist_id), None)
        if not original_playlist:
            scheduler_logger.error(f"❌ Could not find original playlist data for {scheduled_playlist.navidrome_playlist_id}")
            return

        # The seed was stored in artist_id as "radio:{seed_type}:{seed_id}"
        stored_seed = original_playlist.get("artist_id", "")
        parts = stored_seed.split(":", 2)
        if len(parts) != 3 or parts[0] != "radio":
            scheduler_logger.error(f"❌ Malformed radio seed '{stored_seed}' for playlist {scheduled_playlist.navidrome_playlist_id}")
            return
        seed_type, seed_id = parts[1], parts[2]

        library_ids = original_playlist.get("library_ids") or None
        original_length = original_playlist.get("playlist_length", 25)

        # Resolve seed and gather fresh candidates
        processor = RadioProcessor(nav_client)
        seed = await processor.resolve_seed(seed_type, seed_id, library_ids)
        candidate_tracks = await processor.gather_candidate_tracks(seed, library_ids)
        if not candidate_tracks:
            scheduler_logger.warning(f"⚠️ No candidate tracks for radio refresh of {scheduled_playlist.navidrome_playlist_id}")
            return

        library_stats = await nav_client.get_library_stats()
        await apply_loved_signal(candidate_tracks)
        filtered_tracks, _ = filter_tracks_for_this_is_playlist(
            source_tracks=candidate_tracks,
            target_playlist_size=original_length,
            library_stats=library_stats
        )

        # Encourage a fresh arrangement relative to the previous run
        previous_songs = song_labels(original_playlist.get("songs", []))[:10]
        variety_instruction = (
            f"REFRESH: The previous station opened with: {', '.join(previous_songs[:5])}. "
            f"Keep it on-theme but vary the selection and ordering for a fresh listen."
        ) if previous_songs else ""

        curated_track_ids, reasoning, album_suggestions = await ai_client_instance.curate_radio(
            seed_name=seed["name"],
            tracks_json=filtered_tracks,
            num_tracks=original_length,
            include_reasoning=True,
            variety_context=variety_instruction
        )

        if not curated_track_ids:
            scheduler_logger.warning(f"⚠️ No curated tracks generated for radio playlist {scheduled_playlist.navidrome_playlist_id}")
            return

        # Keep the seed-artist cap and the seed-first rule across refreshes,
        # not just on first creation
        track_by_id = {track["id"]: track for track in candidate_tracks}
        curated_tracks, _ = cap_seed_artist(
            [track_by_id[tid] for tid in curated_track_ids if tid in track_by_id],
            seed,
            num_tracks=original_length,
            candidate_pool=candidate_tracks
        )
        curated_tracks = promote_seed_first(curated_tracks, seed, candidate_tracks)
        curated_track_ids = [track["id"] for track in curated_tracks]

        await nav_client.update_playlist(
            playlist_id=scheduled_playlist.navidrome_playlist_id,
            track_ids=curated_track_ids,
            comment=reasoning if reasoning else None
        )

        track_summaries = summarise_tracks(candidate_tracks, order=curated_track_ids)

        # Record how this rebuild went, exactly as creation does. A scheduled
        # rebuild has nobody watching it, so this is the only way the listener
        # ever learns the station came back short or from a degraded pool.
        shortfall = build_shortfall(
            requested=original_length,
            delivered=len(curated_tracks),
            candidate_pool_size=len(candidate_tracks),
            distinct_artists=count_distinct_artists(curated_tracks),
            warnings=processor.pool_warnings
        )
        album_suggestions = [
            {**suggestion, "lidarr_url": lidarr_add_url(suggestion.get("artist"), suggestion.get("album"))}
            for suggestion in (album_suggestions or [])
        ]
        if shortfall["is_short"]:
            scheduler_logger.info(
                f"📻 Radio refresh: '{scheduled_playlist.navidrome_playlist_id}' came up "
                f"{shortfall['missing']} track(s) short ({len(curated_tracks)}/{original_length})"
            )

        await db.update_playlist_content(
            navidrome_playlist_id=scheduled_playlist.navidrome_playlist_id,
            songs=track_summaries,
            reasoning=reasoning,
            album_suggestions=album_suggestions,
            build_info=shortfall
        )

        await advance_schedule(
            scheduled_playlist, db,
            f"Radio playlist {scheduled_playlist.navidrome_playlist_id}"
        )

    except Exception as e:
        scheduler_logger.error(f"❌ Error refreshing Radio playlist {scheduled_playlist.navidrome_playlist_id}: {e}")
        if propagate_errors:
            raise

@app.get("/api/playlists")
async def get_all_playlists(db: DatabaseManager = Depends(get_db)):
    """Get all playlists with scheduling information"""
    try:
        playlists = await db.get_all_playlists_with_schedule_info()
        # Present every row's tracks in one shape, whichever era it was written in
        for playlist in playlists:
            songs = playlist.get("songs", [])
            playlist["songs"] = normalise_stored_songs(songs if isinstance(songs, list) else [])
            playlist["track_count"] = len(playlist["songs"])
        return playlists
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch playlists: {str(e)}")

def infer_playlist_type(playlist: dict) -> str:
    """Work out which refresh path rebuilds this playlist.

    `playlist_type` is only recorded on the scheduled_playlists row, so a
    playlist that was never scheduled has to be identified from the key it
    stored in artist_id: Radio writes "radio:{seed_type}:{seed_id}" and
    Re-Discover writes a fixed sentinel. Anything else is a "This Is" artist id
    (Genre Mix stores a genre name there, but has no refresh path at all, so it
    falls through to the same error either way).
    """
    if playlist.get("playlist_type"):
        return playlist["playlist_type"]

    artist_id = playlist.get("artist_id") or ""
    if artist_id.startswith("radio:"):
        return "radio"
    if artist_id in ("rediscover", "rediscover_v2"):
        return "rediscover"
    return "this_is"


@app.post("/api/playlists/{playlist_id}/recreate")
async def recreate_playlist(
    playlist_id: int,
    options: RecreatePlaylistRequest = RecreatePlaylistRequest(),
    db: DatabaseManager = Depends(get_db)
):
    """Rebuild a playlist now, rather than waiting for its schedule.

    Reuses the scheduler's own refresh paths so a manual rebuild and an
    automatic one produce the same result — including the Radio rules (seed
    first, seed artist capped). Unlike the scheduled run, errors are propagated
    so the button can report what went wrong instead of failing silently.
    """
    playlist = await db.get_playlist_by_id_with_schedule_info(playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    navidrome_playlist_id = playlist.get("navidrome_playlist_id")
    if not navidrome_playlist_id:
        raise HTTPException(
            status_code=400,
            detail="This playlist isn't linked to a Navidrome playlist, so it can't be rebuilt"
        )

    playlist_type = infer_playlist_type(playlist)
    refreshers = {
        "radio": refresh_radio_playlist,
        "this_is": refresh_this_is_playlist,
        "rediscover": refresh_rediscover_playlist,
    }
    refresher = refreshers.get(playlist_type)
    if not refresher:
        raise HTTPException(
            status_code=400,
            detail=f"Don't know how to rebuild a '{playlist_type}' playlist"
        )

    # Apply any requested changes BEFORE rebuilding, so the new settings are what
    # the rebuild actually uses rather than taking effect a run late.
    if options.playlist_length is not None:
        if options.playlist_length < 1:
            raise HTTPException(status_code=400, detail="playlist_length must be at least 1")
        await db.update_playlist_length(playlist_id, options.playlist_length)
        scheduler_logger.info(
            f"🔁 Recreate: target length for '{playlist.get('playlist_name')}' "
            f"set to {options.playlist_length}"
        )

    refresh_frequency = playlist.get("refresh_frequency")
    scheduled_id = playlist.get("scheduled_id")
    if options.refresh_frequency is not None and options.refresh_frequency != refresh_frequency:
        if options.refresh_frequency not in ("none", "never", "daily", "weekly", "monthly"):
            raise HTTPException(
                status_code=400,
                detail=f"Unknown refresh frequency '{options.refresh_frequency}'"
            )
        # Replace rather than mutate: there is no update-frequency method, and a
        # schedule is only ever one row per Navidrome playlist.
        await db.delete_scheduled_playlist_by_navidrome_id(navidrome_playlist_id)
        scheduled_id = None
        refresh_frequency = options.refresh_frequency
        if refresh_frequency not in ("none", "never"):
            await db.create_scheduled_playlist(
                playlist_type=playlist_type,
                navidrome_playlist_id=navidrome_playlist_id,
                refresh_frequency=refresh_frequency,
                next_refresh=calculate_next_refresh(refresh_frequency)
            )
            refreshed_schedule = await db.get_playlist_by_id_with_schedule_info(playlist_id)
            scheduled_id = (refreshed_schedule or {}).get("scheduled_id")
        schedule_playlist_refresh()
        scheduler_logger.info(
            f"🔁 Recreate: schedule for '{playlist.get('playlist_name')}' "
            f"set to {refresh_frequency}"
        )

    # Re-read so the rebuild picks up a changed length
    playlist = await db.get_playlist_by_id_with_schedule_info(playlist_id) or playlist

    target = ManualRefreshTarget(
        navidrome_playlist_id=navidrome_playlist_id,
        playlist_type=playlist_type,
        refresh_frequency=refresh_frequency,
        scheduled_id=scheduled_id
    )

    scheduler_logger.info(
        f"🔁 Manual recreate requested for {playlist_type} playlist "
        f"'{playlist.get('playlist_name')}' ({navidrome_playlist_id})"
    )

    try:
        await refresher(target, db, propagate_errors=True)
    except Exception as e:
        error_msg = str(e)
        if "Invalid username or password" in error_msg or "No authentication method available" in error_msg:
            raise HTTPException(status_code=401, detail=error_msg)
        if "Network error" in error_msg or "connecting to Navidrome" in error_msg:
            raise HTTPException(status_code=503, detail=f"Cannot connect to Navidrome server: {error_msg}")
        raise HTTPException(status_code=500, detail=f"Failed to recreate playlist: {error_msg}")

    refreshed = await db.get_playlist_by_id_with_schedule_info(playlist_id)
    songs = normalise_stored_songs((refreshed or {}).get("songs", []))
    return {
        "playlist_id": playlist_id,
        "playlist_name": (refreshed or playlist).get("playlist_name"),
        "playlist_type": playlist_type,
        "track_count": len(songs),
        "songs": songs,
        "reasoning": (refreshed or {}).get("reasoning"),
        # Same detail a first build returns, so a rebuild shows the same panel
        "album_suggestions": (refreshed or {}).get("album_suggestions") or [],
        "shortfall": (refreshed or {}).get("build_info"),
    }


@app.delete("/api/playlists/{playlist_id}")
async def delete_playlist(playlist_id: int, db: DatabaseManager = Depends(get_db)):
    """Delete a playlist from both local database and Navidrome"""
    try:
        # First, get the specific playlist to find the Navidrome playlist ID
        # Use a direct query instead of fetching all playlists
        playlist = await db.get_playlist_by_id_with_schedule_info(playlist_id)
        
        if not playlist:
            raise HTTPException(status_code=404, detail="Playlist not found")
        
        # Delete from Navidrome if we have a playlist ID
        navidrome_playlist_id = playlist.get("navidrome_playlist_id")
        if navidrome_playlist_id:
            nav_client = get_navidrome_client()
            try:
                print(f"🗑️ Deleting playlist {playlist_id} from Navidrome (Navidrome ID: {navidrome_playlist_id})")
                deletion_result = await nav_client.delete_playlist(navidrome_playlist_id)
                print(f"✅ Navidrome deletion result: {deletion_result}")
            except Exception as e:
                print(f"❌ Warning: Failed to delete playlist from Navidrome: {e}")
                # Continue with local deletion even if Navidrome deletion fails
        else:
            print(f"⚠️ No Navidrome playlist ID found for local playlist {playlist_id}, skipping Navidrome deletion")
        
        # Delete from scheduled playlists if it exists
        if navidrome_playlist_id:
            await db.delete_scheduled_playlist_by_navidrome_id(navidrome_playlist_id)
        
        # Delete from local database
        success = await db.delete_playlist(playlist_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Playlist not found in database")
        
        return {"message": "Playlist deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete playlist: {str(e)}")

@app.get("/api/recipes")
async def get_available_recipes():
    """Get information about available playlist generation recipes"""
    try:
        recipes_info = recipe_manager.list_available_recipes()
        return recipes_info
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load recipes: {str(e)}")

@app.get("/api/recipes/validate")
async def validate_recipes():
    """Validate all recipe files and return any errors"""
    try:
        registry = recipe_manager._load_registry()
        validation_results = {}
        
        for playlist_type, recipe_filename in registry.items():
            errors = recipe_manager.validate_recipe(recipe_filename)
            validation_results[playlist_type] = {
                "recipe_file": recipe_filename,
                "valid": len(errors) == 0,
                "errors": errors
            }
        
        return validation_results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to validate recipes: {str(e)}")

@app.get("/api/scheduler/status")
async def get_scheduler_status():
    """Get scheduler status and active jobs"""
    try:
        global scheduler
        if scheduler:
            jobs = list(scheduler.get_jobs())
            job_info = []
            for job in jobs:
                job_info.append({
                    "id": job.id,
                    "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                    "func": job.func.__name__ if hasattr(job, 'func') else str(job.func)
                })
            
            return {
                "scheduler_running": scheduler.running,
                "active_jobs": len(jobs),
                "jobs": job_info,
                "scheduler_state": str(scheduler.state)
            }
        else:
            return {
                "scheduler_running": False,
                "error": "Scheduler not initialized"
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get scheduler status: {str(e)}")

@app.post("/api/scheduler/trigger")
async def trigger_scheduler_check():
    """Manually trigger the scheduler to check for playlists due for refresh"""
    try:
        scheduler_logger.info("🧪 Manual scheduler trigger requested via API")
        await refresh_scheduled_playlists()
        return {"message": "Scheduler check completed successfully"}
    except Exception as e:
        scheduler_logger.error(f"❌ Error in manual scheduler trigger: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to trigger scheduler: {str(e)}")

@app.post("/api/scheduler/start")
async def start_scheduler_job():
    """Manually start the recurring scheduler job"""
    try:
        schedule_playlist_refresh()
        global scheduler
        jobs = list(scheduler.get_jobs()) if scheduler else []
        scheduler_logger.info(f"🔄 Scheduler job registration requested. Active jobs: {len(jobs)}")
        return {
            "message": "Scheduler job started",
            "active_jobs": len(jobs),
            "jobs": [{"id": job.id, "next_run": job.next_run_time.isoformat() if job.next_run_time else None} for job in jobs]
        }
    except Exception as e:
        scheduler_logger.error(f"❌ Error starting scheduler job: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start scheduler job: {str(e)}")

@app.get("/api/ai-model-info")
async def get_ai_model_info():
    """Get current AI model information for analytics"""
    try:
        ai_client_instance = get_ai_client()
        return {
            "provider": ai_client_instance.provider.provider_type,
            "model": ai_client_instance.model or "unknown",
            "has_api_key": bool(ai_client_instance.api_key)
        }
    except Exception as e:
        return {
            "provider": "unknown",
            "model": "unknown", 
            "has_api_key": False
        }

@app.post("/api/track-library-size")
async def track_library_size(db: DatabaseManager = Depends(get_db)):
    """Track library size for analytics (called post-launch)"""
    try:
        # Check if we should track (90+ days since last tracking)
        should_track = await db.should_track_library_size()
        if not should_track:
            return {"message": "Library size tracking not needed yet", "tracked": False}
        
        # Get Navidrome client and query library size
        nav_client = get_navidrome_client()
        song_count = await nav_client.get_total_song_count()
        
        # Get or create user ID and record the data
        user_id = await db.get_or_create_user_id()
        await db.record_library_size(song_count)
        
        scheduler_logger.info(f"📊 Library size tracked: {song_count} songs for user {user_id}")
        
        return {
            "message": "Library size tracked successfully",
            "tracked": True,
            "song_count": song_count,
            "user_id": user_id
        }
        
    except Exception as e:
        scheduler_logger.error(f"❌ Error tracking library size: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to track library size: {str(e)}")

# SPA ROUTING - Smart catch-all for client-side routing (MUST be last route)
@app.get("/{path:path}", response_class=HTMLResponse)
async def spa_router(request: Request, path: str):
    """Handle SPA routing - serve app for known paths, redirect unknown paths"""
    # Known SPA paths - serve the app and let frontend handle routing
    spa_paths = ["this-is", "re-discover", "genre-mix", "radio", "playlists", "terms"]
    
    if path in spa_paths:
        # Apply same system check logic as root
        if not system_check_passed:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/system-check", status_code=302)
        return render_index(request)
    
    # Unknown paths - redirect to home
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/", status_code=302)

if __name__ == "__main__":
    # Custom logging config to filter out Umami heartbeat requests
    import uvicorn.config
    import uvicorn.logging

    class FilteredUvicornFormatter(uvicorn.logging.AccessFormatter):
        def format(self, record):
            # Filter out GET / requests (Umami heartbeats) from access logs
            if hasattr(record, 'args') and record.args:
                # Look for GET / HTTP patterns in the log message
                message = str(record.args[2]) if len(record.args) > 2 else ""
                if 'GET / HTTP' in message:
                    return ""  # Return empty string to suppress this log
            return super().format(record)
    
    # Configure uvicorn with custom formatter
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["access"]["()"] = FilteredUvicornFormatter
    
    # The app serves on 4545 everywhere — direct run, the Docker image's CMD, and
    # the compose 4545:4545 mapping. Overridable via PORT.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "4545")),
        log_config=log_config
    )