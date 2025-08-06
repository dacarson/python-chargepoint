#!/usr/bin/env python3
"""
Simple test script to verify token caching functionality.
"""

import tempfile
import os
from pathlib import Path

from python_chargepoint.token_cache import TokenCache


def test_token_cache():
    """Test the token cache functionality."""
    print("Testing token cache functionality...")
    
    # Create a temporary directory for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Using temporary directory: {temp_dir}")
        
        # Initialize cache
        cache = TokenCache(cache_dir=temp_dir)
        print("✓ TokenCache initialized")
        
        # Test saving a token
        username = "testuser"
        session_token = "test_token_123"
        user_id = "12345"
        
        cache.save_token(username, session_token, user_id, expires_in_hours=1)
        print("✓ Token saved to cache")
        
        # Test loading the token
        cached_data = cache.load_token(username)
        if cached_data:
            print("✓ Token loaded from cache")
            print(f"  Username: {cached_data['username']}")
            print(f"  User ID: {cached_data['user_id']}")
            print(f"  Token: {cached_data['session_token'][:10]}...")
        else:
            print("✗ Failed to load token from cache")
            return False
        
        # Test clearing the token
        cache.clear_token(username)
        cached_data = cache.load_token(username)
        if cached_data is None:
            print("✓ Token cleared from cache")
        else:
            print("✗ Failed to clear token from cache")
            return False
        
        print("✓ All token cache tests passed!")
        return True


if __name__ == "__main__":
    success = test_token_cache()
    if success:
        print("\n🎉 Token caching is working correctly!")
    else:
        print("\n❌ Token caching tests failed!")
        exit(1) 