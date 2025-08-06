#!/usr/bin/env python3
"""
Example script demonstrating ChargePoint token caching functionality.

This script shows how to use the token caching feature to avoid
re-authentication on script restarts.
"""

import sys
import logging
from getpass import getpass

from python_chargepoint import ChargePoint
from python_chargepoint.constants import _LOGGER


def setup_logging():
    """Setup logging for the example."""
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(levelname)s: %(message)s")
    stream_handler.setFormatter(formatter)
    _LOGGER.addHandler(stream_handler)
    _LOGGER.setLevel(logging.DEBUG)


def main():
    """Main example function."""
    setup_logging()
    
    print("=== ChargePoint Token Caching Example ===\n")
    
    # Get user credentials
    username = input("ChargePoint Username: ")
    password = getpass("Password: ")
    
    print("\n--- First Run (Fresh Login) ---")
    try:
        # Create client with token caching enabled (default)
        client = ChargePoint(username, password, use_token_cache=True)
        print("✓ Login successful! Token cached for future use.")
        
        # Get account info to verify login
        account = client.get_account()
        print(f"✓ Account verified: {account.user.full_name}")
        
    except Exception as e:
        print(f"✗ Login failed: {e}")
        return
    
    print("\n--- Second Run (Cached Token) ---")
    try:
        # Create client again - should use cached token
        client2 = ChargePoint(username, password, use_token_cache=True)
        print("✓ Login successful using cached token!")
        
        # Get account info to verify login
        account2 = client2.get_account()
        print(f"✓ Account verified: {account2.user.full_name}")
        
    except Exception as e:
        print(f"✗ Cached login failed: {e}")
        return
    
    print("\n--- Token Cache Management ---")
    try:
        # Demonstrate cache management
        client2.clear_token_cache()
        print("✓ Token cache cleared for current user")
        
        # Try to login again - should require fresh authentication
        client3 = ChargePoint(username, password, use_token_cache=True)
        print("✓ Fresh login successful after cache clear!")
        
    except Exception as e:
        print(f"✗ Cache management failed: {e}")
        return
    
    print("\n=== Example Complete ===")
    print("Token caching is working! The script will now:")
    print("1. Cache tokens automatically on successful login")
    print("2. Reuse cached tokens on subsequent runs")
    print("3. Fall back to fresh login if cached token is expired")
    print("4. Clear cache on logout or manual cache clearing")


if __name__ == "__main__":
    main() 