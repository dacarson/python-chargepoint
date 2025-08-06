import json
import os
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

from .constants import _LOGGER


class TokenCache:
    """Manages caching of ChargePoint session tokens to disk."""
    
    def __init__(self, cache_dir: Optional[str] = None):
        """
        Initialize the token cache.
        
        Args:
            cache_dir: Directory to store cache files. Defaults to ~/.chargepoint/
        """
        if cache_dir is None:
            cache_dir = os.path.expanduser("~/.chargepoint")
        
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        _LOGGER.debug("Token cache directory: %s", self.cache_dir)
    
    def _get_cache_file(self, username: str) -> Path:
        """Get the cache file path for a username."""
        # Create a hash of the username to avoid special characters in filename
        username_hash = hashlib.sha256(username.encode()).hexdigest()[:16]
        return self.cache_dir / f"token_{username_hash}.json"
    
    def save_token(self, username: str, session_token: str, user_id: str, 
                   expires_in_hours: int = 24) -> None:
        """
        Save a session token to disk with expiration.
        
        Args:
            username: The ChargePoint username
            session_token: The session token to cache
            user_id: The user ID associated with the token
            expires_in_hours: Hours until token expires (default: 24)
        """
        cache_data = {
            "username": username,
            "session_token": session_token,
            "user_id": user_id,
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(hours=expires_in_hours)).isoformat()
        }
        
        cache_file = self._get_cache_file(username)
        try:
            with open(cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
            _LOGGER.debug("Saved token cache for user: %s", username)
        except Exception as e:
            _LOGGER.warning("Failed to save token cache: %s", e)
    
    def load_token(self, username: str) -> Optional[Dict[str, Any]]:
        """
        Load a cached session token.
        
        Args:
            username: The ChargePoint username
            
        Returns:
            Dictionary with token data if valid, None if expired or not found
        """
        cache_file = self._get_cache_file(username)
        
        if not cache_file.exists():
            _LOGGER.debug("No cached token found for user: %s", username)
            return None
        
        try:
            with open(cache_file, 'r') as f:
                cache_data = json.load(f)
            
            # Check if token is expired
            expires_at = datetime.fromisoformat(cache_data["expires_at"])
            if datetime.now() > expires_at:
                _LOGGER.debug("Cached token expired for user: %s", username)
                self.clear_token(username)
                return None
            
            _LOGGER.debug("Loaded cached token for user: %s", username)
            return cache_data
            
        except Exception as e:
            _LOGGER.warning("Failed to load token cache: %s", e)
            # Remove corrupted cache file
            try:
                cache_file.unlink()
            except:
                pass
            return None
    
    def clear_token(self, username: str) -> None:
        """Remove cached token for a username."""
        cache_file = self._get_cache_file(username)
        try:
            if cache_file.exists():
                cache_file.unlink()
                _LOGGER.debug("Cleared token cache for user: %s", username)
        except Exception as e:
            _LOGGER.warning("Failed to clear token cache: %s", e)
    
    def clear_all_tokens(self) -> None:
        """Remove all cached tokens."""
        try:
            for cache_file in self.cache_dir.glob("token_*.json"):
                cache_file.unlink()
            _LOGGER.debug("Cleared all token caches")
        except Exception as e:
            _LOGGER.warning("Failed to clear all token caches: %s", e) 