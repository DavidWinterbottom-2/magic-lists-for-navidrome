import httpx
import os
import json
from typing import List, Dict, Any, Union, Tuple, Optional
from .ai_response import extract_json_payload
from .recipe_manager import recipe_manager
from .services.ai_providers import get_ai_provider

class AIClient:
    """Client for AI-powered track curation using configurable providers"""
    
    def __init__(self):
        self.provider = get_ai_provider()
        # Backward compatibility - keep these for fallback logic
        self.api_key = self.provider.api_key
        self.model = self.provider.model
        self.base_url = self.provider.base_url

        # Debug logging
        print(f"🔍 AIClient initialized with provider: {self.provider.provider_type}")
        print(f"🤖 Using model: {self.model}")
        print(f"🌐 Base URL: {self.base_url}")
        
        
    async def curate_this_is(
        self, 
        artist_name: str, 
        tracks_json: List[Dict[str, Any]], 
        num_tracks: int = 20,
        include_reasoning: bool = False,
        variety_context: str = None
    ) -> Union[List[str], Tuple[List[str], str]]:
        """Curate a 'This Is' playlist for a single artist using AI
        
        Args:
            artist_name: Name of the artist
            tracks_json: List of track dictionaries with id, title, album, year, play_count
            num_tracks: Number of tracks to select (default: 20)
            include_reasoning: Whether to return AI's reasoning along with track IDs
            
        Returns:
            List of track IDs in curated order, or tuple of (track_ids, reasoning) if include_reasoning=True
        """
        
        if not self.api_key and self.provider.provider_type == "openrouter":
            print(f"❌ No AI API key configured, using fallback curation for {artist_name}")
            # Processing tracks for curation (logging moved to scheduler_logger)
            # Fallback: return first num_tracks by play count
            sorted_tracks = sorted(
                tracks_json,
                key=lambda x: x.get("play_count", 0),
                reverse=True
            )
            track_ids = [track["id"] for track in sorted_tracks[:num_tracks]]

            if include_reasoning:
                fallback_reasoning = f"Fallback curation: Selected {len(track_ids)} tracks sorted by play count (highest first). No AI API key configured."
                return track_ids, fallback_reasoning
            else:
                return track_ids
        
        try:
            # Using AI to curate playlist (logging moved to scheduler_logger)
            
            # SHUFFLE tracks to prevent AI from album-grouping based on input order
            import random
            shuffled_tracks = tracks_json.copy()  # Don't modify the original list
            random.shuffle(shuffled_tracks)
            
            # Note: We now pass shuffled_tracks directly as clean JSON array to the AI
            # No more string conversion and text blob parsing!
            
            # Log track data completeness
            original_track_count = len(tracks_json)
            shuffled_track_count = len(shuffled_tracks)
            
            print(f"🎵 Preparing {shuffled_track_count} tracks for AI curation")
            
            # Verify track data includes essential fields
            if shuffled_tracks:
                sample_track = shuffled_tracks[0]
                essential_fields = ['id', 'title', 'artist', 'album']
                missing_fields = [field for field in essential_fields if field not in sample_track]
                if missing_fields:
                    print(f"⚠️  Missing essential fields in tracks: {missing_fields}")
            else:
                print(f"❌ ERROR: No tracks available for curation!")
            
            # Use recipe system to generate prompt and get LLM parameters
            recipe_inputs = {
                "artists": artist_name,
                "num_tracks": num_tracks,
                "variety_context": variety_context or ""
            }
            
            print(f"🍳 Applying recipe for {artist_name} ({num_tracks} tracks)")
            
            final_recipe = recipe_manager.apply_recipe("this_is", recipe_inputs, include_reasoning)
            
            # Check if this is new recipe format (has llm_config) or legacy format
            if "llm_config" in final_recipe:
                # New recipe format
                llm_config = final_recipe.get("llm_config", {})
                model_instructions = final_recipe.get("model_instructions", "")
                
                # Use model from environment (.env file), ignoring recipe model_name
                model = self.model or "openai/gpt-3.5-turbo"
                temperature = llm_config.get("temperature", 0.7)
                max_tokens = llm_config.get("max_output_tokens", 1000)
                
                print(f"🤖 Using AI model: {model} (from {self.provider.provider_type} provider)")

                # Serialize the complete recipe (excluding tracks_data to avoid duplication)
                recipe_without_tracks = {k: v for k, v in final_recipe.items() if k != "tracks_data"}

                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
                
                # Build structured JSON payload with INDEX-BASED approach
                # Create indexed tracks (remove complex IDs, use simple indices)
                indexed_tracks = []
                track_id_map = []  # Keep mapping of index → actual track ID
                
                for index, track in enumerate(shuffled_tracks):
                    # Store the actual track ID in our mapping
                    track_id_map.append(track["id"])
                    
                    # Create indexed track (minimal essential data to reduce token usage)
                    indexed_track = {
                        "index": index,
                        "track_name": track.get("title", "Unknown"),
                        "album": track.get("album", "Unknown"),
                        "year": track.get("year", 0),
                        "play_count": track.get("play_count", 0),
                        "local_library_likes": track.get("local_library_likes", False)
                    }
                    indexed_tracks.append(indexed_track)
                
                structured_payload = {
                    "recipe": recipe_without_tracks,
                    "available_tracks": indexed_tracks,  # INDEX-BASED tracks (no complex IDs)
                    "request": {
                        "artist_name": artist_name,
                        "desired_track_count": num_tracks,
                        "playlist_type": "this_is"
                     }
                }

                print(f"🔢 Using index-based approach for {len(track_id_map)} tracks")

                # Minimal payload for "This Is" - only essential data
                user_content = f"""Select up to {num_tracks} tracks for a "This Is {artist_name}" playlist. If fewer than {num_tracks} tracks are available, select all available tracks.

Tracks: {json.dumps(indexed_tracks, separators=(',', ':'), ensure_ascii=False)}

Return JSON: {{"track_ids": [indices], "reasoning": "summary"}}"""
                
                payload = {
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": model_instructions
                        },
                        {
                            "role": "user", 
                            "content": user_content
                        }
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature
                }
                
                print(f"💬 Sending structured payload to AI")
                
                # DEBUG: Dump payload to file for "This Is" playlist inspection

            else:
                # Legacy recipe format
                prompt = final_recipe["prompt"]
                llm_params = final_recipe["llm_params"]
                
                # Use model from environment first, only fallback to recipe if not set
                model = self.model or llm_params.get("model_fallback", "openai/gpt-3.5-turbo")
                temperature = llm_params.get("temperature", 0.7)
                max_tokens = llm_params.get("max_tokens", 1000)
                

                
                system_prompt = "You are a professional music curator. Always respond with valid JSON containing track_ids array and reasoning string. No other text outside the JSON."
            
            
            # Use the provider to make the AI request
            if "llm_config" in final_recipe:
                # New recipe format - use structured payload
                content = await self.provider.generate(
                    system_prompt=model_instructions,
                    user_prompt=user_content,
                    max_tokens=max_tokens,
                    temperature=temperature
                )
            else:
                # Legacy recipe format
                content = await self.provider.generate(
                    system_prompt="You are a professional music curator. Always respond with valid JSON containing track_ids array and reasoning string. No other text outside the JSON.",
                    user_prompt=prompt,
                    max_tokens=max_tokens,
                    temperature=temperature
                )

            # Log the full raw AI response for debugging
            print(f"🤖 FULL RAW AI RESPONSE for This Is: {content}")

            # Parse the JSON response with comprehensive validation
            try:
                # Salvage the JSON out of whatever the model returned — fences,
                # preamble, trailing commas and nested objects all handled in
                # one place (backend/ai_response.py).
                final_json = extract_json_payload(content)

                # Try to parse the extracted JSON
                response_data = json.loads(final_json)

                # Validate response structure with index-based approach
                source_track_count = len(tracks_json)
                
                if isinstance(response_data, dict) and "track_ids" in response_data:
                    # New format with reasoning - validate structure
                    track_ids = response_data.get("track_ids", [])
                    reasoning = response_data.get("reasoning", "")
                    
                    # Structure checks
                    if not isinstance(track_ids, list):
                        print(f"❌ Response validation failed: track_ids is not a list")
                        raise ValueError("Response structure invalid: track_ids must be a list")
                    
                    if not isinstance(reasoning, str):
                        print(f"❌ Response validation failed: reasoning is not a string")
                        raise ValueError("Response structure invalid: reasoning must be a string")

                    # INDEX-BASED: Validate all track IDs are integers (indices)
                    if not all(isinstance(tid, int) for tid in track_ids):
                        print(f"❌ Response validation failed: not all track_ids are integers")
                        raise ValueError("Invalid track_ids format: all IDs must be integers (indices)")
                    
                    returned_track_count = len(track_ids)

                    # Simplified validation - focus on response quality
                    # Check 1: AI returned some tracks
                    if returned_track_count == 0:
                        print(f"❌ AI returned no tracks - invalid response")
                        raise ValueError("AI response validation failed: No tracks returned")

                    # Check 2: Reasonable upper bound
                    max_reasonable = int(num_tracks * 1.5)  # Allow up to 1.5x requested for minor flexibility
                    if returned_track_count > max_reasonable:
                        print(f"❌ AI returned {returned_track_count} tracks, much more than requested {num_tracks}")
                        raise ValueError(f"AI response validation failed: Too many tracks returned ({returned_track_count} vs requested {num_tracks})")

                    # Check 3: Allow AI to return more indices than available tracks (for duplicates to reach target count)
                    # Note: Invalid indices will be filtered out later, duplicates are allowed

                    print(f"✅ AI returned {returned_track_count} tracks (requested: {num_tracks}), validation passed")

                    # INDEX-BASED: Map indices back to actual track IDs
                    # Find which indices are invalid (out of range)
                    invalid_indices = [idx for idx in track_ids if idx < 0 or idx >= len(track_id_map)]
                    if invalid_indices:
                        print(f"❌ AI returned {len(invalid_indices)} invalid indices out of {len(track_ids)}")
                    
                    # Map valid indices to actual track IDs
                    valid_indices = [idx for idx in track_ids if 0 <= idx < len(track_id_map)]
                    mapped_track_ids = [track_id_map[idx] for idx in valid_indices]
                    # Mapped indices to track IDs
                    
                    # Final selection (limit to requested count)
                    final_selection = mapped_track_ids[:num_tracks]
                    
                    # AI curation successful for Re-Discover Weekly (logging moved to scheduler_logger)
                    if reasoning:
                        # AI reasoning available (logged in main.py scheduler_logger)
                        pass

                    # Final selection (limit to requested count)
                    final_selection = mapped_track_ids[:num_tracks]

                    if include_reasoning:
                        return final_selection, reasoning
                    else:
                        return final_selection

                # Handle simple array format (legacy)
                elif isinstance(response_data, list) and all(isinstance(tid, str) for tid in response_data):
                    valid_ids = {track["id"] for track in tracks_json}
                    filtered_ids = [tid for tid in response_data if tid in valid_ids]
                    final_selection = filtered_ids[:num_tracks]

                    # AI curation successful for Genre Mix (logging moved to scheduler_logger)

                    if include_reasoning:
                        return final_selection, ""  # No reasoning available
                    else:
                        return final_selection
                else:
                    raise ValueError("Invalid response format: expected dict with track_ids or array of track IDs")

            except (json.JSONDecodeError, ValueError) as e:
                print(f"Failed to parse AI response: {e}")
                print(f"Response content: {content}")
                # Fall back to simple selection
                return self._fallback_rediscover_selection(tracks_json, num_tracks, include_reasoning)

        except httpx.RequestError as e:
            print(f"🌐 Network error calling AI API: {e}")
            print(f"🔑 API Key present: {bool(self.api_key)}")
            print(f"🌐 Base URL: {self.base_url}")
            return self._fallback_rediscover_selection(tracks_json, num_tracks, include_reasoning, f"Network error: {e}")
        except httpx.HTTPStatusError as e:
            response_text = e.response.text
            
            # Detect HTML error pages (like Cloudflare 502 errors) and truncate for logging
            if (response_text.strip().startswith('<!DOCTYPE html') or 
                response_text.strip().startswith('<html') or
                len(response_text) > 500):
                
                # Truncate long responses for clean logging
                truncated_text = response_text[:200] + "..." if len(response_text) > 200 else response_text
                print(f"🚨 HTTP error from AI API: {e.response.status_code} - {truncated_text}")
                
                # User-friendly error for common infrastructure issues
                if e.response.status_code in [502, 503, 504]:
                    user_message = f"AI service temporarily unavailable (error {e.response.status_code}). Please try again in a minute."
                else:
                    user_message = f"AI service error (HTTP {e.response.status_code}). Please try again."
                    
                return self._fallback_rediscover_selection(tracks_json, num_tracks, include_reasoning, user_message)
            else:
                # Normal error response, log as before
                print(f"🚨 HTTP error from AI API: {e.response.status_code} - {response_text}")
                print(f"🔑 API Key present: {bool(self.api_key)}")
                print(f"🤖 Model: {self.model}")
                return self._fallback_rediscover_selection(tracks_json, num_tracks, include_reasoning, f"HTTP {e.response.status_code}: {response_text}")
        except Exception as e:
            print(f"💥 Unexpected error in Re-Discover Weekly AI curation: {e}")
            import traceback
            print(f"📋 Traceback: {traceback.format_exc()}")
            return self._fallback_rediscover_selection(tracks_json, num_tracks, include_reasoning, f"Unexpected error: {e}")

    async def curate_rediscover_weekly(
        self,
        candidate_tracks: List[Dict[str, Any]],
        analysis_summary: str,
        num_tracks: int = 20,
        include_reasoning: bool = True,
        variety_context: str = None
    ) -> Union[List[str], Tuple[List[str], str]]:
        """Curate a Re-Discover Weekly playlist using AI

        Args:
            candidate_tracks: List of pre-filtered candidate tracks with metadata
            analysis_summary: Summary of the algorithmic analysis performed
            num_tracks: Number of tracks to select (default: 20)
            include_reasoning: Whether to return AI's reasoning along with track IDs
            variety_context: Additional context for variety (optional)

        Returns:
            List of track IDs in curated order, or tuple of (track_ids, reasoning) if include_reasoning=True
        """

        if not self.api_key and self.provider.provider_type == "openrouter":
            print(f"❌ No AI API key configured, using fallback curation for Re-Discover Weekly")
            # Fallback: return first num_tracks by score (should already be sorted by rediscover algorithm)
            track_ids = [track["id"] for track in candidate_tracks[:num_tracks]]

            if include_reasoning:
                fallback_reasoning = f"Fallback curation: Selected top {len(track_ids)} tracks from algorithmic scoring (highest score first). No AI API key configured."
                return track_ids, fallback_reasoning
            else:
                return track_ids

        # Build indexed tracks (remove complex IDs, use simple indices)
        indexed_tracks = []
        track_id_map = []  # Keep mapping of index → actual track ID

        for index, track in enumerate(candidate_tracks):
            # Store the actual track ID in our mapping
            track_id_map.append(track["id"])

            # Create indexed track (minimal metadata to reduce prompt size)
            indexed_track = {
                "index": index,
                "track_name": track.get("title", "Unknown"),
                "artist": track.get("artist", "Unknown"),
                "genre": track.get("genre", "Unknown"),
                "rediscovery_score": round(track.get("rediscovery_score", 0), 1)
            }
            indexed_tracks.append(indexed_track)

        try:
            print(f"🤖 Making AI request for Re-Discover Weekly curation...")

            # Use recipe system with proper placeholder replacement
            recipe_inputs = {
                "analysis_summary": analysis_summary,
                "num_tracks": num_tracks
            }

            final_recipe = recipe_manager.apply_recipe("re_discover", recipe_inputs)

            # Check if this is new recipe format (has llm_config) or legacy format
            if "llm_config" in final_recipe:
                # New recipe format with placeholders properly replaced
                llm_config = final_recipe.get("llm_config", {})
                model_instructions = final_recipe.get("model_instructions", "")

                # Use model from environment (.env file), ignoring recipe model_name
                model = self.model or "openai/gpt-3.5-turbo"
                temperature = llm_config.get("temperature", 0.7)
                max_tokens = llm_config.get("max_output_tokens", 1500)

                print(f"🤖 Using AI model: {model} (from {self.provider.provider_type} provider)")

                # Serialize the complete recipe (excluding tracks for structured payload)
                recipe_without_tracks = {k: v for k, v in final_recipe.items() if k not in ["candidate_tracks", "tracks_data"]}

                structured_payload = {
                    "recipe": recipe_without_tracks,
                    "available_tracks": indexed_tracks,  # INDEX-BASED tracks (no complex IDs)
                    "analysis_summary": analysis_summary,
                    "request": {
                        "desired_track_count": num_tracks,
                        "playlist_type": "rediscover",
                        "variety_context": variety_context or ""
                    }
                }

                # Minimal payload for re-discover - only essential data
                user_content = f"""Select {num_tracks} tracks for a Re-Discover Weekly playlist.

Tracks: {json.dumps(indexed_tracks, separators=(',', ':'), ensure_ascii=False)}

Return JSON: {{"track_ids": [indices], "reasoning": "summary"}}"""

                print(f"📤 Phase 2 AI Payload (first 500 chars): {user_content[:500]}...")
                print(f"📤 Phase 2 AI Payload (structured_tracks count): {len(indexed_tracks)}")

                content = await self.provider.generate(
                    system_prompt=model_instructions,
                    user_prompt=user_content,
                    max_tokens=max_tokens,
                    temperature=temperature
                )
            else:
                # Legacy recipe format fallback
                prompt = final_recipe.get("prompt", "")
                llm_params = final_recipe.get("llm_params", {})

                model = self.model or llm_params.get("model_fallback", "openai/gpt-3.5-turbo")
                temperature = llm_params.get("temperature", 0.8)
                max_tokens = llm_params.get("max_tokens", 2500)

                content = await self.provider.generate(
                    system_prompt="You are a professional music curator specializing in rediscovery playlists. Always respond with valid JSON containing track_ids array and reasoning string. No other text outside the JSON.",
                    user_prompt=prompt,
                    max_tokens=max_tokens,
                    temperature=temperature
                )

            # Parse the JSON response with comprehensive validation
            try:
                # Salvage the JSON out of whatever the model returned — fences,
                # preamble, trailing commas and nested objects all handled in
                # one place (backend/ai_response.py).
                final_json = extract_json_payload(content)

                # Try to parse the extracted JSON
                result = json.loads(final_json)

                # Validate response structure with index-based approach
                if isinstance(result, dict) and "track_ids" in result:
                    # New format with reasoning - validate structure
                    track_indices = result.get("track_ids", [])
                    reasoning = result.get("reasoning", "")

                    # Structure checks
                    if not isinstance(track_indices, list):
                        print(f"❌ Response validation failed: track_ids is not a list")
                        raise ValueError("Response structure invalid: track_ids must be a list")

                    if not isinstance(reasoning, str):
                        print(f"❌ Response validation failed: reasoning is not a string")
                        raise ValueError("Response structure invalid: reasoning must be a string")

                    print(f"✅ Response validation passed: {len(track_indices)} track indices, reasoning length: {len(reasoning)}")

                    # Map indices back to actual track IDs
                    track_ids = []
                    for index in track_indices:
                        if 0 <= index < len(track_id_map):
                            track_ids.append(track_id_map[index])
                        else:
                            print(f"⚠️ Invalid track index {index}, skipping")

                    print(f"🔄 Mapped {len(track_ids)} track IDs from {len(track_indices)} indices")

                    # Ensure we have the right number of tracks
                    if len(track_ids) < num_tracks and len(candidate_tracks) >= num_tracks:
                        # Fill with remaining tracks if AI didn't provide enough
                        used_indices = set(track_indices)
                        remaining_tracks = [track_id_map[i] for i in range(len(track_id_map)) if i not in used_indices]
                        track_ids.extend(remaining_tracks[:num_tracks - len(track_ids)])
                        print(f"🔄 Filled to {len(track_ids)} tracks with remaining candidates")

                    print(f"✅ Phase 2 AI curation successful: returning {len(track_ids)} tracks with reasoning length {len(reasoning)}")

                    if include_reasoning:
                        return track_ids, reasoning
                    else:
                        return track_ids

                else:
                    print(f"❌ Response validation failed: expected dict with 'track_ids' key, got: {type(result)}")
                    raise ValueError("Response structure invalid: missing track_ids")

            except json.JSONDecodeError as e:
                print(f"❌ Failed to parse AI response as JSON: {e}")
                print(f"🔍 Raw response: {content}")
                return self._fallback_rediscover_selection(candidate_tracks, num_tracks, include_reasoning, f"AI returned invalid JSON: {e}")
            except Exception as e:
                print(f"❌ Failed to validate AI response: {e}")
                print(f"🔍 Raw response: {content}")
                return self._fallback_rediscover_selection(candidate_tracks, num_tracks, include_reasoning, f"AI response validation failed: {e}")

        except Exception as e:
            print(f"💥 Unexpected error in Re-Discover Weekly AI curation: {e}")
            import traceback
            print(f"📋 Traceback: {traceback.format_exc()}")
            return self._fallback_rediscover_selection(candidate_tracks, num_tracks, include_reasoning, f"Unexpected error: {e}")

    def _fallback_rediscover_selection(self, candidate_tracks: List[Dict[str, Any]], num_tracks: int, include_reasoning: bool = False, error_reason: str = "AI service was unavailable") -> Union[List[str], Tuple[List[str], str]]:
        """Fallback selection algorithm for rediscover when AI is unavailable"""
        # Use the pre-sorted candidates (should already be sorted by score)
        track_ids = [track["id"] for track in candidate_tracks[:num_tracks]]
        
        if include_reasoning:
            reasoning = f"Fallback curation: Selected top {len(track_ids)} tracks from algorithmic pre-filtering (sorted by play count × days since last play). {error_reason}"
            return track_ids, reasoning
        else:
            return track_ids

    async def call_ai(self, llm_config: Dict[str, Any]) -> Union[str, Dict[str, Any]]:
        """Generic method to call AI with llm_config from recipes"""
        try:
            model = self.model or llm_config.get("model_fallback", "openai/gpt-3.5-turbo")
            temperature = llm_config.get("temperature", 0.7)
            max_tokens = llm_config.get("max_output_tokens", 1500)

            # Get system and user prompts from llm_config
            system_prompt = llm_config.get("system_prompt", "You are a helpful AI assistant.")
            user_prompt = llm_config.get("user_prompt", "")

            print(f"🤖 Making generic AI call with model {model}...")

            content = await self.provider.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                temperature=temperature
            )

            # Try to parse as JSON, return as string if not
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return content

        except Exception as e:
            print(f"💥 Error in generic AI call: {e}")
            raise

    async def curate_genre_mix(
        self,
        genre: str,
        tracks_json: List[Dict[str, Any]],
        num_tracks: int = 20,
        include_reasoning: bool = False,
        variety_context: Optional[str] = None
    ) -> Union[List[str], Tuple[List[str], str]]:
        """Curate a 'Genre Mix' playlist for a specific genre using AI

        Args:
            genre: Name of the genre
            tracks_json: List of track dictionaries with id, title, album, year, play_count
            num_tracks: Number of tracks to select (default: 20)
            include_reasoning: Whether to return AI's reasoning along with track IDs
            variety_context: Additional context for variety (optional)

        Returns:
            List of track IDs in curated order, or tuple of (track_ids, reasoning) if include_reasoning=True
        """

        if not self.api_key and self.provider.provider_type == "openrouter":
            print(f"❌ No AI API key configured, using fallback curation for {genre}")
            # Fallback: return first num_tracks by play count
            sorted_tracks = sorted(
                tracks_json,
                key=lambda x: x.get("play_count", 0),
                reverse=True
            )
            track_ids = [track["id"] for track in sorted_tracks[:num_tracks]]

            if include_reasoning:
                fallback_reasoning = f"Fallback curation: Selected {len(track_ids)} tracks sorted by play count (highest first). No AI API key configured."
                return track_ids, fallback_reasoning
            else:
                return track_ids

        try:
            # Using AI to curate playlist (logging moved to scheduler_logger)

            # SHUFFLE tracks to prevent AI from album-grouping based on input order
            import random
            shuffled_tracks = tracks_json.copy()  # Don't modify the original list
            random.shuffle(shuffled_tracks)

            # Note: We now pass shuffled_tracks directly as clean JSON array to the AI
            # No more string conversion and text blob parsing!

            # Log track data completeness
            original_track_count = len(tracks_json)
            shuffled_track_count = len(shuffled_tracks)

            print(f"🎵 Preparing {shuffled_track_count} tracks for AI curation")

            # Verify track data includes essential fields
            if shuffled_tracks:
                sample_track = shuffled_tracks[0]
                essential_fields = ['id', 'title', 'artist', 'album']
                missing_fields = [field for field in essential_fields if field not in sample_track]
                if missing_fields:
                    print(f"⚠️  Missing essential fields in tracks: {missing_fields}")
            else:
                print(f"❌ ERROR: No tracks available for curation!")

            # Use recipe system to generate prompt and get LLM parameters
            recipe_inputs = {
                "genre": genre,
                "num_tracks": num_tracks,
                "variety_context": variety_context or ""
            }

            print(f"🍳 Applying recipe for {genre} ({num_tracks} tracks)")

            final_recipe = recipe_manager.apply_recipe("genre_mix", recipe_inputs, include_reasoning)

            # Initialize variables
            model_instructions = ""
            user_content = ""
            prompt = ""
            track_id_map = []

            # New recipe format (genre_mix recipe has llm_config)
            llm_config = final_recipe.get("llm_config", {})
            model_instructions = final_recipe.get("model_instructions", "")

            # Use model from environment (.env file), ignoring recipe model_name
            model = self.model or "openai/gpt-3.5-turbo"
            temperature = llm_config.get("temperature", 0.7)
            max_tokens = llm_config.get("max_output_tokens", 16000)

            print(f"🤖 Using AI model: {model} (from {self.provider.provider_type} provider)")

            # Serialize the complete recipe (excluding tracks_data to avoid duplication)
            recipe_without_tracks = {k: v for k, v in final_recipe.items() if k != "tracks_data"}

            # Build structured JSON payload with INDEX-BASED approach
            # Create indexed tracks (remove complex IDs, use simple indices)
            indexed_tracks = []

            for index, track in enumerate(shuffled_tracks):
                # Store the actual track ID in our mapping
                track_id_map.append(track["id"])

                # Create indexed track (minimal essential data to reduce token usage)
                indexed_track = {
                    "index": index,
                    "track_name": track.get("title", "Unknown"),
                    "artist": track.get("artist", "Unknown"),
                    "play_count": track.get("play_count", 0),
                    "local_library_likes": track.get("local_library_likes", False)
                }
                indexed_tracks.append(indexed_track)

            structured_payload = {
                "recipe": recipe_without_tracks,
                "available_tracks": indexed_tracks,  # INDEX-BASED tracks (no complex IDs)
                "request": {
                    "genre_name": genre,
                    "desired_track_count": num_tracks,
                    "playlist_type": "genre_mix"
                }
            }

            print(f"🔢 Using index-based approach for {len(track_id_map)} tracks")

            # Minimal payload for genre mix - only essential data
            user_content = f"""Select {num_tracks} tracks for a {genre} playlist.

Tracks: {json.dumps(indexed_tracks, separators=(',', ':'), ensure_ascii=False)}

Return JSON: {{"track_ids": [indices], "reasoning": "summary"}}"""

            # Use the provider to make the AI request
            content = await self.provider.generate(
                system_prompt=model_instructions,
                user_prompt=user_content,
                max_tokens=max_tokens,
                temperature=temperature
            )

            # Log the full raw AI response for debugging
            print(f"🤖 FULL RAW AI RESPONSE for Genre Mix: {content}")

            # Parse the JSON response with comprehensive validation
            try:
                # Salvage the JSON out of whatever the model returned — fences,
                # preamble, trailing commas and nested objects all handled in
                # one place (backend/ai_response.py).
                final_json = extract_json_payload(content)

                # Try to parse the extracted JSON
                response_data = json.loads(final_json)

                # Validate response structure with index-based approach
                source_track_count = len(tracks_json)

                if isinstance(response_data, dict) and "track_ids" in response_data:
                    # New format with reasoning - validate structure
                    track_ids = response_data.get("track_ids", [])
                    reasoning = response_data.get("reasoning", "")

                    # Structure checks
                    if not isinstance(track_ids, list):
                        print(f"❌ Response validation failed: track_ids is not a list")
                        raise ValueError("Response structure invalid: track_ids must be a list")

                    if not isinstance(reasoning, str):
                        print(f"❌ Response validation failed: reasoning is not a string")
                        raise ValueError("Response structure invalid: reasoning must be a string")

                    # INDEX-BASED: Validate all track IDs are integers (indices)
                    if not all(isinstance(tid, int) for tid in track_ids):
                        print(f"❌ Response validation failed: not all track_ids are integers")
                        raise ValueError("Invalid track_ids format: all IDs must be integers (indices)")

                    returned_track_count = len(track_ids)

                    # Simplified validation - focus on response quality
                    # Check 1: AI returned some tracks
                    if returned_track_count == 0:
                        print(f"❌ AI returned no tracks - invalid response")
                        raise ValueError("AI response validation failed: No tracks returned")

                    # Check 2: Reasonable upper bound
                    max_reasonable = int(num_tracks * 1.5)  # Allow up to 1.5x requested for minor flexibility
                    if returned_track_count > max_reasonable:
                        print(f"❌ AI returned {returned_track_count} tracks, much more than requested {num_tracks}")
                        raise ValueError(f"AI response validation failed: Too many tracks returned ({returned_track_count} vs requested {num_tracks})")

                    # Check 3: Validate tracks are within source bounds
                    if returned_track_count > source_track_count:
                        print(f"❌ AI returned {returned_track_count} tracks but we only provided {source_track_count}")
                        raise ValueError(f"AI response validation failed: More tracks returned than provided")

                    print(f"✅ AI returned {returned_track_count} tracks (requested: {num_tracks}), validation passed")

                    # INDEX-BASED: Map indices back to actual track IDs
                    # Find which indices are invalid (out of range)
                    invalid_indices = [idx for idx in track_ids if idx < 0 or idx >= len(track_id_map)]
                    if invalid_indices:
                        print(f"❌ AI returned {len(invalid_indices)} invalid indices out of {len(track_ids)}")

                    # Map valid indices to actual track IDs
                    valid_indices = [idx for idx in track_ids if 0 <= idx < len(track_id_map)]
                    mapped_track_ids = [track_id_map[idx] for idx in valid_indices]
                    # Mapped indices to track IDs

                    # Final selection (limit to requested count)
                    final_selection = mapped_track_ids[:num_tracks]

                    # AI curation successful for Genre Mix (logging moved to scheduler_logger)
                    if reasoning:
                        # AI reasoning available (logged in main.py scheduler_logger)
                        pass

                    # Final selection (limit to requested count)
                    final_selection = mapped_track_ids[:num_tracks]

                    if include_reasoning:
                        return final_selection, reasoning
                    else:
                        return final_selection

                # Handle simple array format (legacy)
                elif isinstance(response_data, list) and all(isinstance(tid, str) for tid in response_data):
                    valid_ids = {track["id"] for track in tracks_json}
                    filtered_ids = [tid for tid in response_data if tid in valid_ids]
                    final_selection = filtered_ids[:num_tracks]

                    # AI curation successful for Genre Mix (logging moved to scheduler_logger)

                    if include_reasoning:
                        return final_selection, ""  # No reasoning available
                    else:
                        return final_selection
                else:
                    raise ValueError("Invalid response format: expected dict with track_ids or array of track IDs")

            except (json.JSONDecodeError, ValueError) as e:
                print(f"Failed to parse AI response: {e}")
                print(f"Response content: {content}")
                # Fall back to simple selection
                return self._fallback_genre_mix_selection(tracks_json, num_tracks, include_reasoning)

        except httpx.RequestError as e:
            print(f"🌐 Network error calling AI API: {e}")
            print(f"🔑 API Key present: {bool(self.api_key)}")
            print(f"🌐 Base URL: {self.base_url}")
            return self._fallback_genre_mix_selection(tracks_json, num_tracks, include_reasoning, f"Network error: {e}")
        except httpx.HTTPStatusError as e:
            response_text = e.response.text

            # Detect HTML error pages (like Cloudflare 502 errors) and truncate for logging
            if (response_text.strip().startswith('<!DOCTYPE html') or
                response_text.strip().startswith('<html') or
                len(response_text) > 500):

                # Truncate long responses for clean logging
                truncated_text = response_text[:200] + "..." if len(response_text) > 200 else response_text
                print(f"🚨 HTTP error from AI API: {e.response.status_code} - {truncated_text}")

                # User-friendly error for common infrastructure issues
                if e.response.status_code in [502, 503, 504]:
                    user_message = f"AI service temporarily unavailable (error {e.response.status_code}). Please try again in a minute."
                else:
                    user_message = f"AI service error (HTTP {e.response.status_code}). Please try again."
                return self._fallback_genre_mix_selection(tracks_json, num_tracks, include_reasoning, user_message)
            else:
                # Normal error response, log as before
                print(f"🚨 HTTP error from AI API: {e.response.status_code} - {response_text}")
                print(f"🔑 API Key present: {bool(self.api_key)}")
                print(f"🤖 Model: {self.model}")
                return self._fallback_genre_mix_selection(tracks_json, num_tracks, include_reasoning, f"HTTP {e.response.status_code}: {response_text}")
        except Exception as e:
            print(f"💥 Unexpected error in Genre Mix AI curation: {e}")
            import traceback
            print(f"📋 Traceback: {traceback.format_exc()}")
            return self._fallback_genre_mix_selection(tracks_json, num_tracks, include_reasoning, f"Unexpected error: {e}")

    def _fallback_genre_mix_selection(self, tracks_json: List[Dict[str, Any]], num_tracks: int, include_reasoning: bool = False, error_reason: str = "AI service was unavailable") -> Union[List[str], Tuple[List[str], str]]:
        """Fallback selection algorithm for genre mix when AI is unavailable"""
        # Sort by play count (highest first)
        sorted_tracks = sorted(
            tracks_json,
            key=lambda x: x.get("play_count", 0),
            reverse=True
        )
        track_ids = [track["id"] for track in sorted_tracks[:num_tracks]]

        if include_reasoning:
            reasoning = f"Fallback curation: Selected top {len(track_ids)} tracks sorted by play count (highest first). {error_reason}"
            return track_ids, reasoning
        else:
            return track_ids

    async def curate_radio(
        self,
        seed_name: str,
        tracks_json: List[Dict[str, Any]],
        num_tracks: int = 25,
        include_reasoning: bool = True,
        variety_context: Optional[str] = None,
        preferred_album_artists: Optional[List[str]] = None
    ) -> Tuple[List[str], str, List[Dict[str, Any]]]:
        """Curate a 'Radio' playlist seeded from an artist or song using AI.

        Unlike the other curators this always returns a triple of
        (track_ids, reasoning, album_suggestions) because Radio additionally asks
        the model to recommend similar-style albums that are NOT in the library.

        Args:
            seed_name: Human-readable seed label (e.g. "Radiohead" or "Karma Police — Radiohead")
            tracks_json: Candidate tracks with id, title, artist, album, year, genre, play_count
            num_tracks: Number of tracks to select
            include_reasoning: Retained for signature parity (reasoning always returned)
            variety_context: Additional context for refreshes (optional)

        Returns:
            Tuple of (track_ids in curated order, reasoning string, album_suggestions list)
        """

        # Fallback when no AI is configured: pick by popularity, no album suggestions
        if not self.api_key and self.provider.provider_type == "openrouter":
            print(f"❌ No AI API key configured, using fallback radio curation for {seed_name}")
            return self._fallback_radio_selection(tracks_json, num_tracks, "No AI API key configured.")

        try:
            # SHUFFLE tracks to prevent AI from grouping based on input order
            import random
            shuffled_tracks = tracks_json.copy()
            random.shuffle(shuffled_tracks)

            print(f"🎵 Preparing {len(shuffled_tracks)} candidate tracks for Radio curation ({seed_name})")

            recipe_inputs = {
                "radio_seed": seed_name,
                "num_tracks": num_tracks,
                "variety_context": variety_context or ""
            }

            final_recipe = recipe_manager.apply_recipe("radio", recipe_inputs, include_reasoning)

            llm_config = final_recipe.get("llm_config", {})
            model_instructions = final_recipe.get("model_instructions", "")

            model = self.model or "openai/gpt-3.5-turbo"
            temperature = llm_config.get("temperature", 0.7)
            max_tokens = llm_config.get("max_output_tokens", 16000)

            print(f"🤖 Using AI model: {model} (from {self.provider.provider_type} provider)")

            # Build index-based tracks (avoids leaking/duplicating complex IDs)
            indexed_tracks = []
            track_id_map = []
            for index, track in enumerate(shuffled_tracks):
                track_id_map.append(track["id"])
                indexed_tracks.append({
                    "index": index,
                    "track_name": track.get("title", "Unknown"),
                    "artist": track.get("artist", "Unknown"),
                    "album": track.get("album", "Unknown"),
                    "year": track.get("year", 0),
                    "genre": track.get("genre", "Unknown"),
                    "play_count": track.get("play_count", 0),
                    "local_library_likes": track.get("local_library_likes", False)
                })

            print(f"🔢 Using index-based approach for {len(track_id_map)} tracks")

            variety_note = f"\n\n{variety_context}" if variety_context else ""

            # Ground album suggestions in Last.fm's similar-artist data when we have
            # it: these artists genuinely fit the seed and are confirmed absent from
            # the library, so the model picks a real album by them rather than
            # inventing an artist from memory.
            album_note = ""
            if preferred_album_artists:
                artist_list = ", ".join(preferred_album_artists)
                album_note = (
                    f"\n\nFor album_suggestions, prefer these artists — similar to the seed "
                    f"(per Last.fm) and confirmed NOT in the library: {artist_list}. "
                    f"Pick one real, well-known album by each artist you suggest; do not invent releases."
                )

            user_content = f"""Build a "{seed_name} Radio" station of up to {num_tracks} tracks from the candidates below, and suggest similar-style albums NOT in the library.{variety_note}{album_note}

Tracks: {json.dumps(indexed_tracks, separators=(',', ':'), ensure_ascii=False)}

Return JSON: {{"track_ids": [indices], "reasoning": "summary", "album_suggestions": [{{"artist": "...", "album": "...", "year": 2011, "reason": "..."}}]}}"""

            content = await self.provider.generate(
                system_prompt=model_instructions,
                user_prompt=user_content,
                max_tokens=max_tokens,
                temperature=temperature
            )

            print(f"🤖 FULL RAW AI RESPONSE for Radio: {content}")

            # Parse the JSON response
            try:
                # Radio replies carry nested album suggestions; the shared
                # extractor is brace-matched, so nesting survives. It also fixes
                # the greedy variant this replaces, which ran to the LAST brace
                # in the reply and swallowed any trailing prose.
                response_data = json.loads(extract_json_payload(content))

                if not isinstance(response_data, dict) or "track_ids" not in response_data:
                    raise ValueError("Invalid response format: expected dict with track_ids")

                track_indices = response_data.get("track_ids", [])
                reasoning = response_data.get("reasoning", "")
                album_suggestions = response_data.get("album_suggestions", [])

                if not isinstance(track_indices, list):
                    raise ValueError("Response structure invalid: track_ids must be a list")
                if not all(isinstance(tid, int) for tid in track_indices):
                    raise ValueError("Invalid track_ids format: all IDs must be integers (indices)")
                if not track_indices:
                    raise ValueError("AI response validation failed: No tracks returned")

                # Map valid indices back to actual track IDs, preserving order and dropping dupes
                seen = set()
                mapped_track_ids = []
                for idx in track_indices:
                    if 0 <= idx < len(track_id_map):
                        tid = track_id_map[idx]
                        if tid not in seen:
                            seen.add(tid)
                            mapped_track_ids.append(tid)

                final_selection = mapped_track_ids[:num_tracks]

                # Sanitise album suggestions to the expected shape
                clean_suggestions = self._sanitise_album_suggestions(album_suggestions)

                print(f"✅ Radio curation successful: {len(final_selection)} tracks, {len(clean_suggestions)} album suggestions")

                return final_selection, reasoning, clean_suggestions

            except (json.JSONDecodeError, ValueError) as e:
                print(f"Failed to parse Radio AI response: {e}")
                print(f"Response content: {content}")
                return self._fallback_radio_selection(tracks_json, num_tracks, f"AI response could not be parsed: {e}")

        except httpx.RequestError as e:
            print(f"🌐 Network error calling AI API: {e}")
            return self._fallback_radio_selection(tracks_json, num_tracks, f"Network error: {e}")
        except httpx.HTTPStatusError as e:
            print(f"🚨 HTTP error from AI API: {e.response.status_code}")
            return self._fallback_radio_selection(tracks_json, num_tracks, f"HTTP {e.response.status_code}")
        except Exception as e:
            print(f"💥 Unexpected error in Radio AI curation: {e}")
            import traceback
            print(f"📋 Traceback: {traceback.format_exc()}")
            return self._fallback_radio_selection(tracks_json, num_tracks, f"Unexpected error: {e}")

    def _sanitise_album_suggestions(self, suggestions: Any) -> List[Dict[str, Any]]:
        """Validate/normalise the AI's album suggestions into a predictable shape."""
        if not isinstance(suggestions, list):
            return []

        clean = []
        for item in suggestions:
            if not isinstance(item, dict):
                continue
            artist = str(item.get("artist", "")).strip()
            album = str(item.get("album", "")).strip()
            if not artist or not album:
                continue
            clean.append({
                "artist": artist,
                "album": album,
                "year": item.get("year"),
                "reason": str(item.get("reason", "")).strip()
            })
        return clean[:5]

    def _fallback_radio_selection(
        self,
        tracks_json: List[Dict[str, Any]],
        num_tracks: int,
        error_reason: str = "AI service was unavailable"
    ) -> Tuple[List[str], str, List[Dict[str, Any]]]:
        """Fallback radio selection: sort by play count, no album suggestions."""
        sorted_tracks = sorted(
            tracks_json,
            key=lambda x: x.get("play_count", 0),
            reverse=True
        )
        track_ids = [track["id"] for track in sorted_tracks[:num_tracks]]
        reasoning = (
            f"Fallback curation: selected the top {len(track_ids)} candidate tracks by play count. "
            f"Album suggestions require an AI provider. {error_reason}"
        )
        return track_ids, reasoning, []

    async def close(self):
        """Close the HTTP client"""
        try:
            if hasattr(self, 'provider') and self.provider:
                await self.provider.close()
        except Exception as e:
            print(f"Warning: Error closing AI provider: {e}")