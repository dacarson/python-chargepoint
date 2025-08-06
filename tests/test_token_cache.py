import json
import tempfile
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from python_chargepoint.token_cache import TokenCache


class TestTokenCache:
    def test_token_cache_initialization(self):
        """Test token cache initialization with default directory."""
        cache = TokenCache()
        assert cache.cache_dir.exists()
        assert cache.cache_dir.is_dir()
    
    def test_token_cache_custom_directory(self):
        """Test token cache initialization with custom directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = TokenCache(cache_dir=temp_dir)
            assert cache.cache_dir == Path(temp_dir)
            assert cache.cache_dir.exists()
    
    def test_save_and_load_token(self):
        """Test saving and loading a token."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = TokenCache(cache_dir=temp_dir)
            
            # Save token
            username = "testuser"
            session_token = "test_token_123"
            user_id = "12345"
            
            cache.save_token(username, session_token, user_id, expires_in_hours=1)
            
            # Load token
            cached_data = cache.load_token(username)
            
            assert cached_data is not None
            assert cached_data["username"] == username
            assert cached_data["session_token"] == session_token
            assert cached_data["user_id"] == user_id
    
    def test_token_expiration(self):
        """Test that expired tokens are not loaded."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = TokenCache(cache_dir=temp_dir)
            
            username = "testuser"
            session_token = "test_token_123"
            user_id = "12345"
            
            # Save token with very short expiration
            cache.save_token(username, session_token, user_id, expires_in_hours=0)
            
            # Token should be expired immediately
            cached_data = cache.load_token(username)
            assert cached_data is None
    
    def test_clear_token(self):
        """Test clearing a specific token."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = TokenCache(cache_dir=temp_dir)
            
            username = "testuser"
            session_token = "test_token_123"
            user_id = "12345"
            
            # Save token
            cache.save_token(username, session_token, user_id)
            
            # Verify token exists
            assert cache.load_token(username) is not None
            
            # Clear token
            cache.clear_token(username)
            
            # Verify token is gone
            assert cache.load_token(username) is None
    
    def test_clear_all_tokens(self):
        """Test clearing all tokens."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = TokenCache(cache_dir=temp_dir)
            
            # Save multiple tokens
            cache.save_token("user1", "token1", "id1")
            cache.save_token("user2", "token2", "id2")
            
            # Verify tokens exist
            assert cache.load_token("user1") is not None
            assert cache.load_token("user2") is not None
            
            # Clear all tokens
            cache.clear_all_tokens()
            
            # Verify all tokens are gone
            assert cache.load_token("user1") is None
            assert cache.load_token("user2") is None
    
    def test_corrupted_cache_file(self):
        """Test handling of corrupted cache files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = TokenCache(cache_dir=temp_dir)
            
            username = "testuser"
            cache_file = cache._get_cache_file(username)
            
            # Create a corrupted cache file
            with open(cache_file, 'w') as f:
                f.write("invalid json content")
            
            # Should return None for corrupted file
            cached_data = cache.load_token(username)
            assert cached_data is None
            
            # Corrupted file should be removed
            assert not cache_file.exists()
    
    def test_cache_file_structure(self):
        """Test that cache files have the expected structure."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = TokenCache(cache_dir=temp_dir)
            
            username = "testuser"
            session_token = "test_token_123"
            user_id = "12345"
            
            cache.save_token(username, session_token, user_id, expires_in_hours=24)
            
            # Check file structure
            cache_file = cache._get_cache_file(username)
            assert cache_file.exists()
            
            with open(cache_file, 'r') as f:
                data = json.load(f)
            
            # Verify required fields
            assert "username" in data
            assert "session_token" in data
            assert "user_id" in data
            assert "created_at" in data
            assert "expires_at" in data
            
            # Verify data types
            assert isinstance(data["username"], str)
            assert isinstance(data["session_token"], str)
            assert isinstance(data["user_id"], str)
            assert isinstance(data["created_at"], str)
            assert isinstance(data["expires_at"], str) 