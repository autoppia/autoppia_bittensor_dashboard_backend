"""
Validation utilities for the application.
"""
import re
from typing import Optional
from urllib.parse import urlparse


def is_valid_url(url: str) -> bool:
    """
    Validate if a string is a valid URL.
    
    Args:
        url: The URL string to validate
        
    Returns:
        bool: True if valid URL, False otherwise
    """
    if not url or not isinstance(url, str):
        return False
    
    # Allow empty string as valid (no image)
    if url.strip() == "":
        return True
    
    try:
        result = urlparse(url)
        # Check if scheme and netloc are present for valid URLs
        # Only allow HTTP and HTTPS schemes
        return all([result.scheme in ['http', 'https'], result.netloc])
    except Exception:
        return False


def is_valid_image_url(url: str) -> bool:
    """
    Validate if a string is a valid image URL or empty.
    
    Args:
        url: The URL string to validate
        
    Returns:
        bool: True if valid image URL or empty, False otherwise
    """
    if not url or not isinstance(url, str):
        return False
    
    # Allow empty string as valid (no image)
    if url.strip() == "":
        return True
    
    # Allow data URLs (base64 encoded images) - these are valid
    url_lower = url.lower()
    if url_lower.startswith('data:image/'):
        return True
    
    # Check if it's a valid HTTP/HTTPS URL
    if not is_valid_url(url):
        return False
    
    # Check for common image file extensions
    image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.ico']
    
    # Allow URLs with image extensions
    if any(url_lower.endswith(ext) for ext in image_extensions):
        return True
    
    # Allow URLs that might be image services (like CDNs) without explicit extensions
    # This is more permissive but still validates the URL structure
    return True


def validate_miner_image_url(image_url: str) -> str:
    """
    Validate and normalize miner image URL.
    
    Args:
        image_url: The image URL to validate
        
    Returns:
        str: Validated image URL (empty string if invalid)
        
    Raises:
        ValueError: If the URL is invalid and not empty
    """
    if not image_url or not isinstance(image_url, str):
        return ""
    
    # Allow empty string
    if image_url.strip() == "":
        return ""
    
    # Validate the URL
    if not is_valid_image_url(image_url):
        raise ValueError(f"Invalid image URL: {image_url}. Must be a valid URL or empty string.")
    
    return image_url.strip()
